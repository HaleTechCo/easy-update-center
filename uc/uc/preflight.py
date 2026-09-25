"""Checks run before every generation (docs/architecture/generation-pipeline.md#step-1-preflight)."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .config import Settings, read_secret
from .errors import UcError
from .model import Credentials
from .pki import CertInfo, cert_info, key_matches_cert, verify_chain

if TYPE_CHECKING:
    import datetime as dt

GITHUB_PLACEHOLDER = 'uc'
ADAPTER_USERNAME = 'uc'


class PreflightError(UcError):
    """The configuration, signing material or credentials are not fit to generate a release."""


def _is_local(host: str) -> bool:
    if host == 'localhost' or host.endswith('.localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_public_url(url: str, *, allow_local: bool) -> list[str]:
    """Require PUBLIC_URL to be a bare http(s) origin that controllers can reach; return warnings (plain http)."""
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        msg = f'PUBLIC_URL must be an http(s) URL with a host (got {url!r})'
        raise PreflightError(msg)
    if parts.username or parts.password:
        msg = 'PUBLIC_URL must not contain credentials'
        raise PreflightError(msg)
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        msg = f'PUBLIC_URL must be a bare origin such as https://uc.example.com (got {url!r}); upstream routing and jenkins-plugin-cli do not support a path prefix'
        raise PreflightError(msg)
    if not allow_local and _is_local(parts.hostname):
        msg = f'PUBLIC_URL points at {parts.hostname}, which Jenkins controllers cannot reach; set ALLOW_LOCAL_PUBLIC_URL=true for tests'
        raise PreflightError(msg)
    if parts.scheme == 'http':
        return ['PUBLIC_URL uses plain http; signatures protect integrity, but TLS is recommended']
    return []


def check_signing(settings: Settings, now: dt.datetime) -> CertInfo:
    """Require a matching key and certificate, issued by the root CA and valid long enough; return the certificate's info."""
    for path in (settings.signing_key_file, settings.signing_cert_file, settings.root_ca_file):
        if not path.is_file():
            msg = f"missing signing material: {path} (see 'uc pki init')"
            raise PreflightError(msg)
    if not key_matches_cert(settings.signing_key_file, settings.signing_cert_file):
        msg = f'{settings.signing_key_file} does not match {settings.signing_cert_file}'
        raise PreflightError(msg)
    info = cert_info(settings.signing_cert_file)
    days = info.days_left(now)
    if days < settings.certificate_minimum_valid_days:
        msg = f"signing certificate expires in {days} days (minimum {settings.certificate_minimum_valid_days}); run 'uc pki rotate'"
        raise PreflightError(msg)
    if not verify_chain(settings.root_ca_file, settings.signing_cert_file):
        msg = f'{settings.signing_cert_file} is not issued by {settings.root_ca_file}'
        raise PreflightError(msg)
    return info


def load_repository_credentials(settings: Settings) -> tuple[str, str]:
    """Return the repository account (username, password); with the adapter on, only the adapter needs it."""
    user = read_secret(settings.repo_username_file)
    password = read_secret(settings.repo_password_file)
    if not user or not password:
        msg = f'Repository credentials missing: provide {settings.repo_username_file} and {settings.repo_password_file}'
        raise PreflightError(msg)
    return user, password


def load_adapter_token(settings: Settings) -> str:
    """Return the token the repo-adapter service writes when it starts."""
    token = read_secret(settings.repo_adapter_token_file)
    if not token:
        msg = f'repo-adapter token missing: {settings.repo_adapter_token_file}; the repo-adapter service creates it when it starts'
        raise PreflightError(msg)
    return token


def load_credentials(settings: Settings) -> Credentials:
    """Return the credentials for the repository (or the adapter) and GitHub, with placeholders when GitHub has none."""
    if settings.repo_adapter:
        user, password = ADAPTER_USERNAME, load_adapter_token(settings)
    else:
        user, password = load_repository_credentials(settings)
    gh_user = read_secret(settings.github_username_file)
    gh_token = read_secret(settings.github_token_file)
    if gh_user and gh_token:
        return Credentials(user, password, gh_user, gh_token, github_placeholder=False)
    return Credentials(user, password, GITHUB_PLACEHOLDER, GITHUB_PLACEHOLDER, github_placeholder=True)
