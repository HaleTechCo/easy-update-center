"""Checks run before every generation.

See docs/architecture/generation-pipeline.md#step-1-preflight.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .config import (
    NO_CREDENTIALS,
    PLAIN_HTTP,
    Settings,
    UnsafeFileError,
    read_secret,
    read_untrusted,
)
from .errors import UcError
from .model import Credentials
from .pki import CertInfo, cert_info, key_matches_cert, verify_chain

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Iterable
    from pathlib import Path

GITHUB_PLACEHOLDER = 'uc'
ADAPTER_USERNAME = 'uc'


class PreflightError(UcError):
    """Configuration, signing material or credentials not fit to generate a release."""


def _is_local(host: str) -> bool:
    if host == 'localhost' or host.endswith('.localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_public_url(url: str, *, allow_local: bool) -> list[str]:
    """Require PUBLIC_URL to be a bare http(s) origin that controllers can reach.

    Return warnings (plain http).

    Any '@' is refused first, as the settings refuse it, so the messages that quote the
    URL never quote credentials.
    """
    if '@' in url:
        msg = f'PUBLIC_URL {NO_CREDENTIALS}'
        raise PreflightError(msg)
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        msg = f'PUBLIC_URL must be an http(s) URL with a host (got {url!r})'
        raise PreflightError(msg)
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        msg = (
            'PUBLIC_URL must be a bare origin such as https://uc.example.com '
            f'(got {url!r}); upstream routing and jenkins-plugin-cli do not support '
            'a path prefix'
        )
        raise PreflightError(msg)
    if not allow_local and _is_local(parts.hostname):
        msg = (
            f'PUBLIC_URL points at {parts.hostname}, which Jenkins controllers '
            'cannot reach; set ALLOW_LOCAL_PUBLIC_URL=true for tests'
        )
        raise PreflightError(msg)
    if parts.scheme == 'http':
        return [f'PUBLIC_URL uses plain http: {PLAIN_HTTP["PUBLIC_URL"]}']
    return []


def check_signing(settings: Settings, now: dt.datetime) -> CertInfo:
    """Require a matching key and certificate, issued by the root CA, valid long enough.

    Return the certificate's info.
    """
    for path in (
        settings.signing_key_file,
        settings.signing_cert_file,
        settings.root_ca_file,
    ):
        if not path.is_file():
            msg = f"missing signing material: {path} (see 'uc pki init')"
            raise PreflightError(msg)
    if not key_matches_cert(settings.signing_key_file, settings.signing_cert_file):
        msg = f'{settings.signing_key_file} does not match {settings.signing_cert_file}'
        raise PreflightError(msg)
    info = cert_info(settings.signing_cert_file)
    days = info.days_left(now)
    if days < settings.certificate_minimum_valid_days:
        msg = (
            f'signing certificate expires in {days} days (minimum '
            f"{settings.certificate_minimum_valid_days}); run 'uc pki rotate'"
        )
        raise PreflightError(msg)
    if not verify_chain(settings.root_ca_file, settings.signing_cert_file):
        msg = f'{settings.signing_cert_file} is not issued by {settings.root_ca_file}'
        raise PreflightError(msg)
    return info


def load_repository_credentials(settings: Settings) -> tuple[str, str]:
    """Return the repository account (username, password).

    With the adapter on, only the adapter needs it.
    """
    user = read_secret(settings.repo_username_file)
    password = read_secret(settings.repo_password_file)
    if not user or not password:
        msg = (
            f'Repository credentials missing: provide {settings.repo_username_file} '
            f'and {settings.repo_password_file}'
        )
        raise PreflightError(msg)
    return user, password


ADAPTER_TOKEN = re.compile(rb'[0-9a-f]{64}')
"""What the repo-adapter writes: secrets.token_hex(32)."""
ADAPTER_TOKEN_LIMIT = 128


def load_adapter_token(settings: Settings) -> str:
    """Return the token the repo-adapter service writes when it starts.

    It lies on the adapter's volume, which the adapter can write, and goes back to the
    adapter as the repository password: so read it without following a link (to this
    container's own secrets), and accept only what the adapter writes.
    """
    path = settings.repo_adapter_token_file
    refused = 'remove it and restart the repo-adapter service, which writes a new one'
    try:
        data = read_untrusted(path, ADAPTER_TOKEN_LIMIT)
    except UnsafeFileError as exc:
        msg = f'repo-adapter token refused: {exc}; {refused}'
        raise PreflightError(msg) from None
    token = (data or b'').strip()
    if not token:
        msg = (
            f'repo-adapter token missing: {path}; '
            'the repo-adapter service creates it when it starts'
        )
        raise PreflightError(msg)
    if not ADAPTER_TOKEN.fullmatch(token):
        msg = (
            f'repo-adapter token refused: {path} is not 64 lower-case hex digits; '
            f'{refused}'
        )
        raise PreflightError(msg)
    return token.decode('ascii')


def load_credentials(settings: Settings) -> Credentials:
    """Return the credentials for the repository (or the adapter) and GitHub.

    With placeholders when GitHub has none.
    """
    if settings.repo_adapter:
        user, password = ADAPTER_USERNAME, load_adapter_token(settings)
    else:
        user, password = load_repository_credentials(settings)
    gh_user = read_secret(settings.github_username_file)
    gh_token = read_secret(settings.github_token_file)
    if gh_user and gh_token:
        return Credentials(user, password, gh_user, gh_token, github_placeholder=False)
    return Credentials(
        user, password, GITHUB_PLACEHOLDER, GITHUB_PLACEHOLDER, github_placeholder=True
    )


DATA_ENTRIES = (
    '.lock',
    '.status.lock',
    'status.json',
    'live',
    'releases',
    'work',
    'cache',
    'resources',
    'doctor',
)
"""What uc keeps at the top of the data volume.

On a new volume (the image's /data is 1777), /data itself is world-writable and
sticky, so these are what another uid owns.
"""

ADAPTER_ENTRIES = ('token', 'cache', 'tmp', 'report.json')
"""What the repo-adapter keeps in its own volume, mounted at /data/repo-adapter."""


def _owner(path: Path) -> int | None:
    try:
        return path.lstat().st_uid
    except FileNotFoundError:
        return None


def check_data_dir(data_dir: Path, entries: Iterable[str] = DATA_ENTRIES) -> None:
    """Raise PreflightError unless this process can write the data volume.

    That is, create files in data_dir, and own each of entries in it that exists (a
    missing one is created later).
    """
    if not data_dir.is_dir():
        return
    uid = os.getuid()
    others = {
        owner
        for name in entries
        if (owner := _owner(data_dir / name)) not in (None, uid)
    }
    if others or not os.access(data_dir, os.W_OK | os.X_OK):
        msg = (
            f'{data_dir} is not writable by uid {uid}: the data volume belongs to '
            'another user; set UC_UID and UC_GID in .env to its owner, or give it to '
            'yours (docs/reference/configuration.md#compose)'
        )
        raise PreflightError(msg)
