"""Checks run before every generation (spec §6 step 1)."""
from __future__ import annotations

import datetime as dt
import ipaddress
from urllib.parse import urlsplit

from .config import Settings, read_secret
from .errors import UcError
from .model import Credentials
from .pki import CertInfo, cert_info, key_matches_cert, verify_chain

GITHUB_PLACEHOLDER = "uc"


class PreflightError(UcError):
    pass


def _is_local(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_public_url(url: str, allow_local: bool) -> list[str]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PreflightError(f"PUBLIC_URL must be an http(s) URL with a host (got {url!r})")
    if parts.username or parts.password:
        raise PreflightError("PUBLIC_URL must not contain credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise PreflightError(
            f"PUBLIC_URL must be a bare origin such as https://uc.example.com (got {url!r}); "
            "upstream routing and jenkins-plugin-cli do not support a path prefix"
        )
    if not allow_local and _is_local(parts.hostname):
        raise PreflightError(
            f"PUBLIC_URL points at {parts.hostname}, which Jenkins controllers cannot reach; "
            "set ALLOW_LOCAL_PUBLIC_URL=true for tests"
        )
    if parts.scheme == "http":
        return ["PUBLIC_URL uses plain http; signatures protect integrity, but TLS is recommended"]
    return []


def check_signing(settings: Settings, now: dt.datetime) -> CertInfo:
    for path in (settings.signing_key_file, settings.signing_cert_file, settings.root_ca_file):
        if not path.is_file():
            raise PreflightError(f"missing signing material: {path} (see 'uc pki init')")
    if not key_matches_cert(settings.signing_key_file, settings.signing_cert_file):
        raise PreflightError(
            f"{settings.signing_key_file} does not match {settings.signing_cert_file}"
        )
    info = cert_info(settings.signing_cert_file)
    days = info.days_left(now)
    if days < settings.certificate_minimum_valid_days:
        raise PreflightError(
            f"signing certificate expires in {days} days "
            f"(minimum {settings.certificate_minimum_valid_days}); run 'uc pki rotate'"
        )
    if not verify_chain(settings.root_ca_file, settings.signing_cert_file):
        raise PreflightError(
            f"{settings.signing_cert_file} is not issued by {settings.root_ca_file}"
        )
    return info


def load_credentials(settings: Settings) -> Credentials:
    user = read_secret(settings.artifactory_username_file)
    password = read_secret(settings.artifactory_password_file)
    if not user or not password:
        raise PreflightError(
            "Artifactory credentials missing: provide "
            f"{settings.artifactory_username_file} and {settings.artifactory_password_file}"
        )
    gh_user = read_secret(settings.github_username_file)
    gh_token = read_secret(settings.github_token_file)
    if gh_user and gh_token:
        return Credentials(user, password, gh_user, gh_token, github_placeholder=False)
    return Credentials(user, password, GITHUB_PLACEHOLDER, GITHUB_PLACEHOLDER, github_placeholder=True)
