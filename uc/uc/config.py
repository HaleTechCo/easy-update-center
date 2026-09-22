"""Settings from environment variables with per-mode defaults (spec §7.1, §11)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError

MODES = ("online", "airgapped")
BACKENDS = ("mirror", "artifactory")
ACCESS_MODES = ("redirect", "proxy")
RESOURCE_SOURCES = ("github", "url", "bundled")

PUBLIC_ARTIFACTORY_URL = "https://repo.jenkins-ci.org/"
UPSTREAM_MIRROR_URL = "https://get.jenkins.io"
UPSTREAM_POPULARITY_URL = (
    "https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/"
    "plugin-installation-trend/latestNumbers.json"
)
UPSTREAM_TOOL_INSTALLERS_URL = "https://updates.jenkins.io/updates/"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Settings:
    mode: str
    public_url: str
    uc_id: str
    generate_schedule: str
    resources_source: str
    resources_ref: str
    resources_url: str
    resources_sha256: str
    popularity_json_url: str
    tool_installers_url: str
    artifactory_url: str
    artifactory_url_with_port: str
    artifactory_api_url: str
    artifactory_repository: str
    download_backend: str
    download_backend_url: str
    download_access: str
    retain_releases: int
    min_plugins: int
    min_plugin_ratio: float
    certificate_minimum_valid_days: int
    signing_cert_days: int
    stale_after_days: int
    allow_local_public_url: bool
    extra_args: tuple[str, ...]
    data_dir: Path
    overlay_dir: Path
    app_dir: Path
    signing_key_file: Path
    signing_cert_file: Path
    root_ca_file: Path
    artifactory_username_file: Path
    artifactory_password_file: Path
    github_username_file: Path
    github_token_file: Path

    @property
    def downloads_root_url(self) -> str:
        return f"{self.public_url}/download"

    @property
    def jar_dir(self) -> Path:
        return self.app_dir / "generator"

    @property
    def jar(self) -> Path:
        return self.jar_dir / "update-center2.jar"

    @property
    def htaccess_script(self) -> Path:
        return self.app_dir / "site" / "generate-htaccess.sh"

    @property
    def bundled_resources(self) -> Path:
        return self.app_dir / "resources"

    @property
    def templates_dir(self) -> Path:
        return self.app_dir / "templates"

    @property
    def version_tool_dir(self) -> Path:
        return self.app_dir / "tools"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache" / "artifactory"


def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name, "").strip()
    return value if value else default


def _choice(env: Mapping[str, str], name: str, default: str, choices: Sequence[str]) -> str:
    value = _get(env, name, default)
    if value not in choices:
        raise ConfigError(f"{name} must be one of {', '.join(choices)} (got {value!r})")
    return value


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = _get(env, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum} (got {value})")
    return value


def _ratio(env: Mapping[str, str], name: str, default: float) -> float:
    raw = _get(env, name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number (got {raw!r})") from None
    if not 0 <= value <= 1:
        raise ConfigError(f"{name} must be between 0 and 1 (got {value})")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(env, name, "true" if default else "false").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false (got {raw!r})")


def with_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def with_explicit_port(url: str) -> str:
    """Add :443/:80 when missing, so update-center2's Authenticator port check matches (spec §10)."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ConfigError("URLs must not contain credentials; use the secret files instead")
    if parts.port is not None or parts.scheme not in ("http", "https"):
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = 443 if parts.scheme == "https" else 80
    return urlunsplit((parts.scheme, f"{host}:{port}", parts.path, parts.query, parts.fragment))


def read_secret(path: Path) -> str | None:
    """Contents of a secret file, stripped; None when the file is missing or empty."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    mode = _choice(env, "MODE", "", MODES)
    online = mode == "online"

    public_url = _get(env, "PUBLIC_URL").rstrip("/")
    if not public_url:
        raise ConfigError("PUBLIC_URL must be set")

    artifactory_url = _get(env, "ARTIFACTORY_URL", PUBLIC_ARTIFACTORY_URL if online else "")
    repository = _get(env, "ARTIFACTORY_REPOSITORY", "releases" if online else "")
    if not artifactory_url or not repository:
        raise ConfigError("ARTIFACTORY_URL and ARTIFACTORY_REPOSITORY must be set in airgapped mode")
    if not _REPOSITORY_RE.match(repository):
        raise ConfigError(f"ARTIFACTORY_REPOSITORY contains unsupported characters: {repository!r}")
    artifactory_url = with_trailing_slash(artifactory_url)
    artifactory_url_with_port = with_explicit_port(artifactory_url)
    api_url = with_trailing_slash(_get(env, "ARTIFACTORY_API_URL", artifactory_url_with_port + "api/"))

    backend = _choice(env, "DOWNLOAD_BACKEND", "mirror" if online else "artifactory", BACKENDS)
    default_backend_url = UPSTREAM_MIRROR_URL if backend == "mirror" else artifactory_url + repository
    backend_url = _get(env, "DOWNLOAD_BACKEND_URL", default_backend_url).rstrip("/")

    resources_source = _choice(
        env, "RESOURCES_SOURCE", "github" if online else "bundled", RESOURCE_SOURCES
    )
    resources_url = _get(env, "RESOURCES_URL")
    if resources_source == "url" and not resources_url:
        raise ConfigError("RESOURCES_URL must be set when RESOURCES_SOURCE=url")

    secrets_dir = Path(_get(env, "SECRETS_DIR", "/run/secrets"))

    def secret(var: str, name: str) -> Path:
        return Path(_get(env, var, str(secrets_dir / name)))

    return Settings(
        mode=mode,
        public_url=public_url,
        uc_id=_get(env, "UC_ID", "default"),
        generate_schedule=_get(env, "GENERATE_SCHEDULE", "0 */6 * * *"),
        resources_source=resources_source,
        resources_ref=_get(env, "RESOURCES_REF", "master"),
        resources_url=resources_url,
        resources_sha256=_get(env, "RESOURCES_SHA256").lower(),
        # For these two an explicitly empty value means "disabled", so unset and empty differ.
        popularity_json_url=env.get(
            "POPULARITY_JSON_URL", UPSTREAM_POPULARITY_URL if online else ""
        ).strip(),
        tool_installers_url=env.get(
            "TOOL_INSTALLERS_URL", UPSTREAM_TOOL_INSTALLERS_URL if online else ""
        ).strip(),
        artifactory_url=artifactory_url,
        artifactory_url_with_port=artifactory_url_with_port,
        artifactory_api_url=api_url,
        artifactory_repository=repository,
        download_backend=backend,
        download_backend_url=backend_url,
        download_access=_choice(env, "DOWNLOAD_ACCESS", "redirect", ACCESS_MODES),
        retain_releases=_int(env, "RETAIN_RELEASES", 5, minimum=1),
        min_plugins=_int(env, "MIN_PLUGINS", 1),
        min_plugin_ratio=_ratio(env, "MIN_PLUGIN_RATIO", 0.9),
        certificate_minimum_valid_days=_int(env, "CERTIFICATE_MINIMUM_VALID_DAYS", 30),
        signing_cert_days=_int(env, "SIGNING_CERT_DAYS", 365, minimum=1),
        stale_after_days=_int(env, "STALE_AFTER_DAYS", 30),
        allow_local_public_url=_bool(env, "ALLOW_LOCAL_PUBLIC_URL", False),
        extra_args=tuple(_get(env, "UC_EXTRA_ARGS").split()),
        data_dir=Path(_get(env, "UC_DATA_DIR", "/data")),
        overlay_dir=Path(_get(env, "UC_OVERLAY_DIR", "/config/overlay")),
        app_dir=Path(_get(env, "UC_APP_DIR", "/opt/uc")),
        signing_key_file=secret("SIGNING_KEY_FILE", "update-center.key"),
        signing_cert_file=secret("SIGNING_CERT_FILE", "update-center.cert"),
        root_ca_file=secret("ROOT_CA_FILE", "root-ca.crt"),
        artifactory_username_file=secret("ARTIFACTORY_USERNAME_FILE", "artifactory_username"),
        artifactory_password_file=secret("ARTIFACTORY_PASSWORD_FILE", "artifactory_password"),
        github_username_file=secret("GITHUB_USERNAME_FILE", "github_username"),
        github_token_file=secret("GITHUB_TOKEN_FILE", "github_token"),
    )
