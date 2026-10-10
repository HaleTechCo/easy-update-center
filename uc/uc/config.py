"""Settings from the environment, with per-mode defaults (docs/reference/configuration.md).

A setting's value is the environment's, else the registry's default for the mode (uc/uc/settings.py). An empty
environment variable means "not set", except for the settings where empty means "off" (POPULARITY_JSON_URL,
TOOL_INSTALLERS_URL).
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit, urlunsplit

from .errors import ConfigError
from .procure.staging import DEFAULT_STAGING
from .settings import CORE_VERSION, REGISTRY

if TYPE_CHECKING:
    from collections.abc import Mapping
    from urllib.parse import SplitResult

PUBLIC_ARTIFACTORY_URL = 'https://repo.jenkins-ci.org/'
# Where public plugins' and cores' real files live: the default download source, and the URLs uc doctor prints.
PUBLIC_RELEASES_URL = PUBLIC_ARTIFACTORY_URL + 'releases/'
UPSTREAM_MIRROR_URL = 'https://get.jenkins.io'
APP_DIR = Path('/opt/uc')
"""The generator, templates and bundled resources in the uc image."""
INTERNAL = {
    'UC_DATA_DIR': '/data',
    'UC_OVERLAY_DIR': '/config/overlay',
    'SECRETS_DIR': '/run/secrets',
    'REPO_ADAPTER_URL': 'http://repo-adapter:8081/artifactory/',
    'REPO_ADAPTER_DIR': '',
}
"""Container paths that the unit and integration tests point at their own: read from the environment, but not settings (not documented, not in uc config or the example files). An empty REPO_ADAPTER_DIR means repo-adapter/ in the data directory."""
_REPOSITORY_RE = re.compile(r'^[A-Za-z0-9._-]+$')
_SCHEME = re.compile(r'[^/:]+://')  # the scheme split_userinfo keeps in front of the user info uc config masks
_PROXY_SCHEME = re.compile(r'[^/:]+:')  # how urllib's proxy handler finds a scheme: up to the first ':', if anything precedes one
_NOT_IN_HOST = re.compile(r'[\x00-\x20\x7f/?#@]')  # http.client refuses a space or control character; a / ? # or @ is part of a path, query or user info
NO_CREDENTIALS = 'must not contain credentials or an @'
"""What follows the setting's name when a URL setting that never carries credentials holds an '@'; preflight's PUBLIC_URL check says the same."""


@dataclass(frozen=True)
class Settings:
    """Validated configuration for one uc run, with the paths derived from it."""

    mode: str
    public_url: str
    uc_id: str
    generate_schedule: str
    doctor_schedule: str
    doctor_fetch: bool
    doctor_since: str
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
    download_proxy_ca_file: str
    procurement_source_url: str
    procurement_source_set: bool
    procurement_staging_dir: Path
    retain_releases: int
    min_plugin_ratio: float
    certificate_minimum_valid_days: int
    stale_after_days: int
    allow_local_public_url: bool
    tls_cert_file: str
    tls_key_file: str
    extra_args: tuple[str, ...]
    java_tool_options: str
    https_proxy: str
    http_proxy: str
    no_proxy: str
    data_dir: Path
    overlay_dir: Path
    app_dir: Path
    secrets_dir: Path
    signing_key_file: Path
    signing_cert_file: Path
    root_ca_file: Path
    repo_username_file: Path
    repo_password_file: Path
    github_username_file: Path
    github_token_file: Path
    repo_push_auth_file: Path

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
    def public_repository(self) -> bool:
        """Return whether the source repository is repo.jenkins-ci.org, which uc never writes to."""
        return urlsplit(self.repo_url).hostname == urlsplit(PUBLIC_ARTIFACTORY_URL).hostname

    @property
    def maven_url(self) -> str:
        """Return the repository's Maven-layout root, where files are read and written by path."""
        if self.repo_type == 'artifact-keeper':
            return f'{self.repo_url}maven/{self.repo_key}/'
        return f'{self.repo_url}{self.repo_key}/'

    @property
    def repo_adapter_token_file(self) -> Path:
        """File in the repo-adapter's directory holding its access token."""
        return self.repo_adapter_dir / 'token'

    @property
    def repo_adapter_report_file(self) -> Path:
        """The repo-adapter's report.json of what it filled in and dropped."""
        return self.repo_adapter_dir / 'report.json'


class Resolver:
    """Raw setting values: the environment first, then the registry's default for the mode."""

    def __init__(self, env: Mapping[str, str]) -> None:
        """Resolve from env."""
        self.env = env
        self.mode = ''

    def source(self, name: str) -> str:
        """Return where name's value comes from: 'env' or 'default'."""
        if name in self.env and (self.env[name].strip() or REGISTRY[name].empty_means_off):
            return 'env'
        return 'default'

    def raw(self, name: str) -> str:
        """Return name's value as given, stripped, or its default for the mode ('' when the code derives it)."""
        if self.source(name) == 'env':
            return self.env[name].strip()
        return REGISTRY[name].default_for(self.mode)


def _internal(r: Resolver, name: str) -> str:
    return r.env.get(name, '').strip() or INTERNAL[name]


def make_resolver(env: Mapping[str, str] | None = None) -> Resolver:
    """Return a Resolver over env (default: os.environ)."""
    return Resolver(os.environ if env is None else env)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from env (default: os.environ), applying the mode's defaults; raise ConfigError on bad values."""
    return settings_from(make_resolver(env))


def _repository(r: Resolver, *, online: bool) -> tuple[str, str, str]:
    """Return the validated (REPO_TYPE, REPO_URL with a trailing slash, REPO_KEY)."""
    repo_type = _choice(r, 'REPO_TYPE')
    public_repo = online and repo_type == 'artifactory'
    repo_url = r.raw('REPO_URL') or (PUBLIC_ARTIFACTORY_URL if public_repo else '')
    repo_key = r.raw('REPO_KEY') or ('releases' if public_repo else '')
    if not repo_url or not repo_key:
        msg = 'REPO_URL and REPO_KEY must be set (they default to repo.jenkins-ci.org only in online mode with REPO_TYPE=artifactory)'
        raise ConfigError(msg)
    if not _REPOSITORY_RE.match(repo_key):
        msg = f'REPO_KEY contains unsupported characters: {repo_key!r}'
        raise ConfigError(msg)
    _check_credential_free_url('REPO_URL', repo_url, advice='; use the secret files instead')
    return repo_type, with_trailing_slash(repo_url), repo_key


def settings_from(r: Resolver) -> Settings:
    """Build Settings from r's values, applying the mode's defaults; raise ConfigError on bad values."""
    mode = _choice(r, 'MODE')
    r.mode = mode
    online = mode == 'online'

    public_url = r.raw('PUBLIC_URL').rstrip('/')
    if not public_url:
        msg = 'PUBLIC_URL must be set'
        raise ConfigError(msg)
    _check_credential_free_url('PUBLIC_URL', public_url)

    repo_type, repo_url, repo_key = _repository(r, online=online)
    repo_url_with_port = with_explicit_port(repo_url)
    api_url = with_trailing_slash(_checked_url(r, 'REPO_API_URL') or repo_url_with_port + 'api/')

    repo_adapter = _bool(r, 'REPO_ADAPTER', default=not online or repo_type == 'artifact-keeper')
    if repo_type == 'artifact-keeper' and not repo_adapter:
        msg = 'REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on'
        raise ConfigError(msg)
    repo_ca = r.raw('REPO_CA_FILE')

    backend = _choice(r, 'DOWNLOAD_BACKEND')
    backend_url = _explicit_backend_url(r) or (UPSTREAM_MIRROR_URL if backend == 'mirror' else '')
    if not backend_url:
        msg = 'DOWNLOAD_BACKEND=artifactory needs DOWNLOAD_BACKEND_URL'
        raise ConfigError(msg)
    backend_url = backend_url.rstrip('/')
    data_dir = Path(_internal(r, 'UC_DATA_DIR'))

    resources_source = _choice(r, 'RESOURCES_SOURCE')
    resources_url = r.raw('RESOURCES_URL')
    if resources_source == 'url' and not resources_url:
        msg = 'RESOURCES_URL must be set when RESOURCES_SOURCE=url'
        raise ConfigError(msg)
    resources_url = _checked_url(r, 'RESOURCES_URL')

    procurement_source = r.raw('PROCUREMENT_SOURCE_URL')
    _check_credential_free_url('PROCUREMENT_SOURCE_URL', procurement_source)

    secrets_dir = Path(_internal(r, 'SECRETS_DIR'))

    return Settings(
        mode=mode,
        public_url=public_url,
        uc_id=r.raw('UC_ID'),
        generate_schedule=r.raw('GENERATE_SCHEDULE'),
        doctor_schedule=r.raw('DOCTOR_SCHEDULE'),
        doctor_fetch=_bool(r, 'DOCTOR_FETCH'),
        doctor_since=_version(r, 'DOCTOR_SINCE'),
        resources_source=resources_source,
        resources_ref=r.raw('RESOURCES_REF'),
        resources_url=resources_url,
        resources_sha256=r.raw('RESOURCES_SHA256').lower(),
        popularity_json_url=_checked_url(r, 'POPULARITY_JSON_URL'),
        tool_installers_url=_checked_url(r, 'TOOL_INSTALLERS_URL'),
        repo_type=repo_type,
        repo_url=repo_url,
        repo_url_with_port=repo_url_with_port,
        repo_api_url=api_url,
        repo_key=repo_key,
        repo_adapter=repo_adapter,
        repo_adapter_url=with_trailing_slash(_internal(r, 'REPO_ADAPTER_URL')),
        repo_adapter_dir=Path(_internal(r, 'REPO_ADAPTER_DIR') or str(data_dir / 'repo-adapter')),
        repo_ca_file=Path(repo_ca) if repo_ca else None,
        repo_max_archive_mb=_int(r, 'REPO_MAX_ARCHIVE_MB'),
        core_age_days=_int(r, 'CORE_AGE_DAYS'),
        download_backend=backend,
        download_backend_url=backend_url,
        download_access=_choice(r, 'DOWNLOAD_ACCESS'),
        download_proxy_ca_file=r.raw('DOWNLOAD_PROXY_CA_FILE'),
        procurement_source_url=with_trailing_slash(procurement_source or PUBLIC_RELEASES_URL),
        procurement_source_set=bool(procurement_source),
        procurement_staging_dir=DEFAULT_STAGING,
        retain_releases=_int(r, 'RETAIN_RELEASES'),
        min_plugin_ratio=_ratio(r, 'MIN_PLUGIN_RATIO'),
        certificate_minimum_valid_days=_int(r, 'CERTIFICATE_MINIMUM_VALID_DAYS'),
        stale_after_days=_int(r, 'STALE_AFTER_DAYS'),
        allow_local_public_url=_bool(r, 'ALLOW_LOCAL_PUBLIC_URL'),
        tls_cert_file=r.raw('TLS_CERT_FILE'),
        tls_key_file=r.raw('TLS_KEY_FILE'),
        extra_args=tuple(r.raw('UC_EXTRA_ARGS').split()),
        java_tool_options=r.raw('JAVA_TOOL_OPTIONS'),
        https_proxy=_checked_proxy(r, 'HTTPS_PROXY'),
        http_proxy=_checked_proxy(r, 'HTTP_PROXY'),
        no_proxy=r.raw('NO_PROXY'),
        data_dir=data_dir,
        overlay_dir=Path(_internal(r, 'UC_OVERLAY_DIR')),
        app_dir=APP_DIR,
        secrets_dir=secrets_dir,
        signing_key_file=secrets_dir / 'update-center.key',
        signing_cert_file=secrets_dir / 'update-center.cert',
        root_ca_file=secrets_dir / 'root-ca.crt',
        repo_username_file=secrets_dir / 'repo_username',
        repo_password_file=secrets_dir / 'repo_password',
        github_username_file=secrets_dir / 'github_username',
        github_token_file=secrets_dir / 'github_token',
        repo_push_auth_file=secrets_dir / 'repo_push_auth',
    )


def _choice(r: Resolver, name: str) -> str:
    choices = REGISTRY[name].choices
    value = r.raw(name)
    if value not in choices:
        msg = f'{name} must be one of {", ".join(choices)} (got {value!r})'
        raise ConfigError(msg)
    return value


def _explicit_backend_url(r: Resolver) -> str:
    """Return DOWNLOAD_BACKEND_URL as set ('' if unset); an '@' in it, and so any credentials, is refused, since web logs it and sends it to controllers."""
    value = r.raw('DOWNLOAD_BACKEND_URL')
    if value:
        _check_credential_free_url('DOWNLOAD_BACKEND_URL', value, advice='; for proxied downloads put them in secrets/download_proxy_auth')
    return value


def _checked_url(r: Resolver, name: str) -> str:
    """Return name's value, '' if unset; a non-empty value must parse as a URL."""
    value = r.raw(name)
    if value:
        _parse_url(name, value)
    return value


def _checked_proxy(r: Resolver, name: str) -> str:
    """Return the proxy URL name sets, '' if unset, checked as urllib's proxy handler will use it; no message quotes any of it.

    The user info may hold anything (split_userinfo). A value urllib refuses, or whose user info it reads differently
    from split_userinfo (_proxy_hostport), is asked for as scheme://host:port. The host and port urllib would connect to
    must be readable: a port that is not a number, an empty host, a host:port that runs on into a path, query or
    fragment (http://proxy:3128?x, or proxy:3128/ without a scheme), or brackets around anything but an IPv6 address
    cannot be used.
    """
    value = r.raw(name)
    if value:
        hint = '; percent-encode # ? / [ ] @ in a user name or password'
        hostport = _proxy_hostport(value)
        if hostport is None:
            msg = f'{name} is not a valid URL: write it as scheme://host:port{hint}'
            raise ConfigError(msg)
        unreadable = f'{name} is not a valid URL: its host or port cannot be read{hint}'
        if _NOT_IN_HOST.search(hostport):
            raise ConfigError(unreadable)
        host = _parse_url(name, '//' + hostport, hint=hint).hostname  # raises the same message for a port that is not a number or broken brackets
        if not host or (hostport.startswith('[') and not _is_ipv6(host)):  # urlsplit also takes an IPvFuture literal ([v1.x]), which http.client cannot use
            raise ConfigError(unreadable)
    return value


def _is_ipv6(host: str) -> bool:
    """Return whether host is an IPv6 address, the only kind of host http.client connects to in brackets."""
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    return True


def _proxy_hostport(value: str) -> str | None:
    """Return the host:port urllib's proxy handler connects to for a proxy URL (urllib.request._parse_proxy, then proxy_open's unquote).

    It takes a scheme up to the first ':', if anything precedes one. When no '/' follows, the whole value is the
    authority (user:password@proxy:3128); a single '/' it refuses (None); after '//' the authority ends at the first '/'
    after the first '@', or at the first '/' without an '@'. The host:port follows the authority's last '@'. An '@' after
    the authority is None too: urllib would take the host from before that '/', split_userinfo from after the last '@'.
    """
    match = _PROXY_SCHEME.match(value)
    rest = value[match.end() :] if match else value
    if not rest.startswith('/'):
        authority = value
    elif not rest.startswith('//'):
        return None
    else:
        at = rest.find('@')
        end = rest.find('/', 2 if at == -1 else at)
        if end == -1:
            end = len(rest)
        if '@' in rest[end:]:
            return None
        authority = rest[2:end]
    return unquote(authority.rpartition('@')[2])


def split_userinfo(value: str) -> tuple[str, str | None, str]:
    """Split a URL as urllib's proxy handler reads it: (its scheme and '://', '' without one; the user info before the last '@', None without an '@'; the rest, from the host on).

    The user info may hold an unencoded # ? / that urlsplit takes as the end of the host, and without a scheme the whole
    value is user info and host (user:password@proxy:3128). urllib ends the user info at the authority's last '@'; taking
    it to the value's last '@' reads the same wherever the '@'s are all in the user info (_checked_proxy refuses a proxy
    where they are not), and never leaves part of a password in the rest.
    """
    match = _SCHEME.match(value)
    scheme = match.group() if match else ''
    userinfo, at, rest = value[len(scheme) :].rpartition('@')
    return scheme, userinfo if at else None, rest


def _check_credential_free_url(name: str, value: str, *, advice: str = '') -> None:
    """Refuse any '@' in a URL setting that never carries credentials, then parse it (_parse_url).

    urlsplit ends the user info at a # ? or /, so it would read a token followed by one as the host and accept it;
    refusing the '@' first also keeps any later message from quoting the token. advice follows the message.
    """
    if '@' in value:
        msg = f'{name} {NO_CREDENTIALS}{advice}'
        raise ConfigError(msg)
    _parse_url(name, value)


def _parse_url(name: str, value: str, *, hint: str = '') -> SplitResult:
    """Return value split into its parts; a URL urllib cannot parse, or whose port is not a number, is a ConfigError naming the setting.

    The message never includes urllib's reason, which can quote part of the value: a password, say.
    """
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError for a port that is not a number in range
    except ValueError:
        msg = f'{name} is not a valid URL: its host or port cannot be read{hint}'
        raise ConfigError(msg) from None
    return parts


def _version(r: Resolver, name: str) -> str:
    raw = r.raw(name)
    if raw and not CORE_VERSION.match(raw):
        msg = f'{name} must be a Jenkins version such as 2.462.1 (got {raw!r})'
        raise ConfigError(msg)
    return raw


def check_staging_outside_data_dir(raw: str | Path, data_dir: Path, label: str) -> None:
    """Refuse the data volume or a folder in it as the staging folder: the scheduler, the repo-adapter and web mount the data volume, and only the uc service may hold the staging folder.

    Both are taken as the commands would use them, '..' and a relative path resolved against the working directory; no symlink is followed and the filesystem is not touched.
    """
    here = Path.cwd()
    if Path(os.path.normpath(here / raw)).is_relative_to(os.path.normpath(here / data_dir)):
        msg = f'{label} must not be the data volume ({data_dir}) or a folder in it, which the other services mount too: keep it on the ./procurement mount, /procurement by default (got {str(raw)!r})'
        raise ConfigError(msg)


def _int(r: Resolver, name: str) -> int:
    raw = r.raw(name)
    try:
        value = int(raw)
    except ValueError:
        msg = f'{name} must be an integer (got {raw!r})'
        raise ConfigError(msg) from None
    minimum = REGISTRY[name].minimum or 0
    if value < minimum:
        msg = f'{name} must be >= {minimum} (got {value})'
        raise ConfigError(msg)
    return value


def _ratio(r: Resolver, name: str) -> float:
    raw = r.raw(name)
    try:
        value = float(raw)
    except ValueError:
        msg = f'{name} must be a number (got {raw!r})'
        raise ConfigError(msg) from None
    if not 0 <= value <= 1:
        msg = f'{name} must be between 0 and 1 (got {value})'
        raise ConfigError(msg)
    return value


def _bool(r: Resolver, name: str, *, default: bool = False) -> bool:
    raw = (r.raw(name) or ('true' if default else 'false')).lower()
    if raw in {'1', 'true', 'yes', 'on'}:
        return True
    if raw in {'0', 'false', 'no', 'off'}:
        return False
    msg = f'{name} must be true or false (got {raw!r})'
    raise ConfigError(msg)


def with_trailing_slash(url: str) -> str:
    """Return url with a '/' appended unless it already ends in one."""
    return url if url.endswith('/') else url + '/'


def with_explicit_port(url: str) -> str:
    """Add :443/:80 when missing, so update-center2's Authenticator port check matches (docs/architecture/generation-pipeline.md#step-3-environment-for-the-generator)."""
    parts = urlsplit(url)
    if parts.port is not None or parts.scheme not in {'http', 'https'}:
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
