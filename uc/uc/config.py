"""Settings from environment variables with per-mode defaults (spec §7.1, §11)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

MODES = ('online', 'airgapped')
REPO_TYPES = ('artifactory', 'artifact-keeper')
BACKENDS = ('mirror', 'artifactory')
ACCESS_MODES = ('redirect', 'proxy')
RESOURCE_SOURCES = ('github', 'url', 'bundled')

PUBLIC_ARTIFACTORY_URL = 'https://repo.jenkins-ci.org/'
UPSTREAM_MIRROR_URL = 'https://get.jenkins.io'
UPSTREAM_POPULARITY_URL = 'https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/plugin-installation-trend/latestNumbers.json'
UPSTREAM_TOOL_INSTALLERS_URL = 'https://updates.jenkins.io/updates/'
DEFAULT_ADAPTER_URL = 'http://repo-adapter:8081/artifactory/'
UPSTREAM_CORE_AGE_DAYS = 400
_REPOSITORY_RE = re.compile(r'^[A-Za-z0-9._-]+$')


@dataclass(frozen=True)
class Settings:
    """Validated configuration for one uc run, with the paths derived from it."""

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
    repo_type: str
    repo_url: str
    repo_url_with_port: str
    repo_api_url: str
    repo_key: str
    repo_adapter: bool
    repo_adapter_url: str
    repo_adapter_dir: Path
    repo_ca_file: Path | None
    repo_max_archive_mb: int
    core_age_days: int
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
    repo_username_file: Path
    repo_password_file: Path
    github_username_file: Path
    github_token_file: Path

    @property
    def downloads_root_url(self) -> str:
        """Public URL under which the site's download links live."""
        return f'{self.public_url}/download'

    @property
    def jar_dir(self) -> Path:
        """Directory holding the update-center2 generator."""
        return self.app_dir / 'generator'

    @property
    def jar(self) -> Path:
        """The update-center2 jar."""
        return self.jar_dir / 'update-center2.jar'

    @property
    def htaccess_script(self) -> Path:
        """Upstream's generate-htaccess.sh."""
        return self.app_dir / 'site' / 'generate-htaccess.sh'

    @property
    def bundled_resources(self) -> Path:
        """Resources shipped in the image, used when RESOURCES_SOURCE=bundled."""
        return self.app_dir / 'resources'

    @property
    def templates_dir(self) -> Path:
        """Directory of the site templates (static .htaccess rules, index.html)."""
        return self.app_dir / 'templates'

    @property
    def version_tool_dir(self) -> Path:
        """Directory holding the compiled VersionTool class (Jenkins version comparisons)."""
        return self.app_dir / 'tools'

    @property
    def cache_dir(self) -> Path:
        """The generator's download cache, one per source.

        Its keys are URL paths, and /artifactory/<key>/... is the same with the adapter on or off, so each
        gets its own directory: toggling REPO_ADAPTER never reuses what the other one cached (an HTML "POM"
        fetched directly, say).
        """
        return self.data_dir / 'cache' / ('repo-adapter' if self.repo_adapter else 'artifactory')

    @property
    def source_url(self) -> str:
        """Artifactory-style base URL that uc and the jar read: the adapter, or the repository itself."""
        return self.repo_adapter_url if self.repo_adapter else self.repo_url_with_port

    @property
    def source_api_url(self) -> str:
        """Artifactory-style API base URL for AQL queries: the adapter's, or the repository's."""
        return self.repo_adapter_url + 'api/' if self.repo_adapter else self.repo_api_url

    @property
    def repo_adapter_token_file(self) -> Path:
        """File in the repo-adapter's directory holding its access token."""
        return self.repo_adapter_dir / 'token'

    @property
    def repo_adapter_report_file(self) -> Path:
        """The repo-adapter's report.json of what it filled in and dropped."""
        return self.repo_adapter_dir / 'report.json'


def _get(env: Mapping[str, str], name: str, default: str = '') -> str:
    value = env.get(name, '').strip()
    return value or default


def _choice(env: Mapping[str, str], name: str, default: str, choices: Sequence[str]) -> str:
    value = _get(env, name, default)
    if value not in choices:
        msg = f'{name} must be one of {", ".join(choices)} (got {value!r})'
        raise ConfigError(msg)
    return value


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = _get(env, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        msg = f'{name} must be an integer (got {raw!r})'
        raise ConfigError(msg) from None
    if value < minimum:
        msg = f'{name} must be >= {minimum} (got {value})'
        raise ConfigError(msg)
    return value


def _ratio(env: Mapping[str, str], name: str, default: float) -> float:
    raw = _get(env, name, str(default))
    try:
        value = float(raw)
    except ValueError:
        msg = f'{name} must be a number (got {raw!r})'
        raise ConfigError(msg) from None
    if not 0 <= value <= 1:
        msg = f'{name} must be between 0 and 1 (got {value})'
        raise ConfigError(msg)
    return value


def _bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = _get(env, name, 'true' if default else 'false').lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    msg = f'{name} must be true or false (got {raw!r})'
    raise ConfigError(msg)


def with_trailing_slash(url: str) -> str:
    """Return url with a '/' appended unless it already ends in one."""
    return url if url.endswith('/') else url + '/'


def with_explicit_port(url: str) -> str:
    """Add :443/:80 when missing, so update-center2's Authenticator port check matches (spec §10)."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        msg = 'URLs must not contain credentials; use the secret files instead'
        raise ConfigError(msg)
    if parts.port is not None or parts.scheme not in ('http', 'https'):
        return url
    host = parts.hostname or ''
    if ':' in host:
        host = f'[{host}]'
    port = 443 if parts.scheme == 'https' else 80
    return urlunsplit((parts.scheme, f'{host}:{port}', parts.path, parts.query, parts.fragment))


def read_secret(path: Path) -> str | None:
    """Contents of a secret file, stripped; None when the file is missing or empty."""
    try:
        value = path.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return None
    return value or None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from env (default: os.environ), applying the mode's defaults; raise ConfigError on bad values."""
    env = os.environ if env is None else env
    mode = _choice(env, 'MODE', '', MODES)
    online = mode == 'online'

    public_url = _get(env, 'PUBLIC_URL').rstrip('/')
    if not public_url:
        msg = 'PUBLIC_URL must be set'
        raise ConfigError(msg)

    repo_type = _choice(env, 'REPO_TYPE', 'artifactory', REPO_TYPES)
    public_repo = online and repo_type == 'artifactory'
    repo_url = _get(env, 'REPO_URL', PUBLIC_ARTIFACTORY_URL if public_repo else '')
    repo_key = _get(env, 'REPO_KEY', 'releases' if public_repo else '')
    if not repo_url or not repo_key:
        msg = 'REPO_URL and REPO_KEY must be set (they default to repo.jenkins-ci.org only in online mode with REPO_TYPE=artifactory)'
        raise ConfigError(msg)
    if not _REPOSITORY_RE.match(repo_key):
        msg = f'REPO_KEY contains unsupported characters: {repo_key!r}'
        raise ConfigError(msg)
    repo_url = with_trailing_slash(repo_url)
    repo_url_with_port = with_explicit_port(repo_url)
    api_url = with_trailing_slash(_get(env, 'REPO_API_URL', repo_url_with_port + 'api/'))

    adapter_default = not online or repo_type == 'artifact-keeper'
    repo_adapter = _bool(env, 'REPO_ADAPTER', default=adapter_default)
    if repo_type == 'artifact-keeper' and not repo_adapter:
        msg = 'REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on'
        raise ConfigError(msg)
    repo_ca = _get(env, 'REPO_CA_FILE')

    backend = _choice(env, 'DOWNLOAD_BACKEND', 'mirror' if online else 'artifactory', BACKENDS)
    if backend == 'mirror':
        default_backend_url = UPSTREAM_MIRROR_URL
    elif repo_type == 'artifact-keeper':
        default_backend_url = f'{repo_url.rstrip("/")}/maven/{repo_key}'
    else:
        default_backend_url = repo_url + repo_key
    backend_url = _get(env, 'DOWNLOAD_BACKEND_URL', default_backend_url).rstrip('/')
    data_dir = Path(_get(env, 'UC_DATA_DIR', '/data'))

    resources_source = _choice(env, 'RESOURCES_SOURCE', 'github' if online else 'bundled', RESOURCE_SOURCES)
    resources_url = _get(env, 'RESOURCES_URL')
    if resources_source == 'url' and not resources_url:
        msg = 'RESOURCES_URL must be set when RESOURCES_SOURCE=url'
        raise ConfigError(msg)

    secrets_dir = Path(_get(env, 'SECRETS_DIR', '/run/secrets'))

    def secret(var: str, name: str) -> Path:
        return Path(_get(env, var, str(secrets_dir / name)))

    return Settings(
        mode=mode,
        public_url=public_url,
        uc_id=_get(env, 'UC_ID', 'default'),
        generate_schedule=_get(env, 'GENERATE_SCHEDULE', '0 */6 * * *'),
        resources_source=resources_source,
        resources_ref=_get(env, 'RESOURCES_REF', 'master'),
        resources_url=resources_url,
        resources_sha256=_get(env, 'RESOURCES_SHA256').lower(),
        # For these two an explicitly empty value means "disabled", so unset and empty differ.
        popularity_json_url=env.get('POPULARITY_JSON_URL', UPSTREAM_POPULARITY_URL if online else '').strip(),
        tool_installers_url=env.get('TOOL_INSTALLERS_URL', UPSTREAM_TOOL_INSTALLERS_URL if online else '').strip(),
        repo_type=repo_type,
        repo_url=repo_url,
        repo_url_with_port=repo_url_with_port,
        repo_api_url=api_url,
        repo_key=repo_key,
        repo_adapter=repo_adapter,
        repo_adapter_url=with_trailing_slash(_get(env, 'REPO_ADAPTER_URL', DEFAULT_ADAPTER_URL)),
        repo_adapter_dir=Path(_get(env, 'REPO_ADAPTER_DIR', str(data_dir / 'repo-adapter'))),
        repo_ca_file=Path(repo_ca) if repo_ca else None,
        repo_max_archive_mb=_int(env, 'REPO_MAX_ARCHIVE_MB', 512, minimum=1),
        core_age_days=_int(env, 'CORE_AGE_DAYS', UPSTREAM_CORE_AGE_DAYS if online else 0),
        download_backend=backend,
        download_backend_url=backend_url,
        download_access=_choice(env, 'DOWNLOAD_ACCESS', 'redirect', ACCESS_MODES),
        retain_releases=_int(env, 'RETAIN_RELEASES', 5, minimum=1),
        min_plugins=_int(env, 'MIN_PLUGINS', 1),
        min_plugin_ratio=_ratio(env, 'MIN_PLUGIN_RATIO', 0.9),
        certificate_minimum_valid_days=_int(env, 'CERTIFICATE_MINIMUM_VALID_DAYS', 30),
        signing_cert_days=_int(env, 'SIGNING_CERT_DAYS', 365, minimum=1),
        stale_after_days=_int(env, 'STALE_AFTER_DAYS', 30),
        allow_local_public_url=_bool(env, 'ALLOW_LOCAL_PUBLIC_URL', default=False),
        extra_args=tuple(_get(env, 'UC_EXTRA_ARGS').split()),
        data_dir=data_dir,
        overlay_dir=Path(_get(env, 'UC_OVERLAY_DIR', '/config/overlay')),
        app_dir=Path(_get(env, 'UC_APP_DIR', '/opt/uc')),
        signing_key_file=secret('SIGNING_KEY_FILE', 'update-center.key'),
        signing_cert_file=secret('SIGNING_CERT_FILE', 'update-center.cert'),
        root_ca_file=secret('ROOT_CA_FILE', 'root-ca.crt'),
        repo_username_file=secret('REPO_USERNAME_FILE', 'repo_username'),
        repo_password_file=secret('REPO_PASSWORD_FILE', 'repo_password'),
        github_username_file=secret('GITHUB_USERNAME_FILE', 'github_username'),
        github_token_file=secret('GITHUB_TOKEN_FILE', 'github_token'),
    )
