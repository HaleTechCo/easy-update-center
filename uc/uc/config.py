"""Settings from the environment and config/uc.toml, with per-mode defaults (docs/reference/configuration.md).

A setting's value is the first of: the environment, uc.toml, the registry's default for the mode (uc/uc/settings.py).
An empty environment variable means "not set" and falls through, except for the settings where empty means "off"
(POPULARITY_JSON_URL, TOOL_INSTALLERS_URL).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from .errors import ConfigError
from .settings import CORE_VERSION, REGISTRY, SECTIONS, SETTINGS, env_name, toml_key, toml_label

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping
    from urllib.parse import SplitResult

    from .settings import Setting

PUBLIC_ARTIFACTORY_URL = 'https://repo.jenkins-ci.org/'
# Where public plugins' and cores' real files live: the default download source, and the URLs uc doctor prints.
PUBLIC_RELEASES_URL = PUBLIC_ARTIFACTORY_URL + 'releases/'
UPSTREAM_MIRROR_URL = 'https://get.jenkins.io'
DEFAULT_CONFIG_FILE = Path('/config/uc.toml')
_REPOSITORY_RE = re.compile(r'^[A-Za-z0-9._-]+$')
_SCHEME = re.compile(r'[^/:]+://')  # what urllib's proxy handler takes as a scheme
_NO_AUTHORITY = re.compile(r'[^/:]+:/(?!/)')  # urllib's proxy handler takes a scheme up to the first ':' and refuses a single '/' after it
_FILE_KEYS = tuple(sorted(toml_label(setting.env) for setting in SETTINGS if not setting.env_only))
_FILE_SECTIONS = tuple(f'[{section}]' for section in SECTIONS)

PROCESS_ENV = ('JAVA_TOOL_OPTIONS', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY')
"""Settings read straight from the process environment, by the JVM and by Python's urllib."""


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
    doctor_json: bool
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
    repo_adapter_port: int
    repo_ca_file: Path | None
    repo_max_archive_mb: int
    core_age_days: int
    download_backend: str
    download_backend_url: str
    download_access: str
    download_proxy_auth_file: Path
    download_proxy_ca_file: str
    procurement_source_url: str
    procurement_source_set: bool
    retain_releases: int
    min_plugins: int
    min_plugin_ratio: float
    certificate_minimum_valid_days: int
    pki_org: str
    pki_root_days: int
    pki_signing_days: int
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
    config_file: Path
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
    """Raw setting values: the environment first, then uc.toml, then the registry's default for the mode."""

    def __init__(self, env: Mapping[str, str], file_values: Mapping[str, str], config_file: Path) -> None:
        """Resolve from env and file_values, which were read from config_file (it may not exist)."""
        self.env = env
        self.file_values = file_values
        self.config_file = config_file
        self.mode = ''

    def source(self, name: str) -> str:
        """Return where name's value comes from: 'env', 'file' or 'default'."""
        if name in self.env and (self.env[name].strip() or REGISTRY[name].empty_means_off):
            return 'env'
        if name in self.file_values:
            return 'file'
        return 'default'

    def raw(self, name: str) -> str:
        """Return name's value as given, stripped, or its default for the mode ('' when the code derives it)."""
        source = self.source(name)
        if source == 'env':
            return self.env[name].strip()
        if source == 'file':
            return self.file_values[name]
        return REGISTRY[name].default_for(self.mode)

    def label(self, name: str) -> str:
        """Return how a message names the setting: the variable, plus its uc.toml key when the value came from the file."""
        if self.source(name) == 'file':
            return f'{name} ({toml_label(name)} in {self.config_file})'
        return name


def make_resolver(env: Mapping[str, str] | None = None, *, config_file: Path | None = None) -> Resolver:
    """Return a Resolver over env (default: os.environ) and the uc.toml at config_file (default: UC_CONFIG_FILE, else /config/uc.toml)."""
    env = os.environ if env is None else env
    path = config_file if config_file is not None else Path(env.get('UC_CONFIG_FILE', '').strip() or DEFAULT_CONFIG_FILE)
    return Resolver(env, read_config_file(path), path)


def load_settings(env: Mapping[str, str] | None = None, *, config_file: Path | None = None) -> Settings:
    """Build Settings from env (default: os.environ) and uc.toml, applying the mode's defaults; raise ConfigError on bad values."""
    return settings_from(make_resolver(env, config_file=config_file))


@dataclass(frozen=True)
class PkiSettings:
    """The defaults uc pki uses for the options it is not given."""

    org: str
    root_days: int
    signing_days: int


def load_pki_settings(env: Mapping[str, str] | None = None, *, config_file: Path | None = None) -> PkiSettings:
    """Return uc pki's defaults (PKI_ORG, PKI_ROOT_DAYS, PKI_SIGNING_DAYS); it needs no other setting, so it runs before MODE and PUBLIC_URL are set."""
    r = make_resolver(env, config_file=config_file)
    return PkiSettings(org=r.raw('PKI_ORG'), root_days=_int(r, 'PKI_ROOT_DAYS'), signing_days=_int(r, 'PKI_SIGNING_DAYS'))


@dataclass(frozen=True)
class WebSettings:
    """The settings the web container reads: what web.env lists (uc/uc/webenv.py)."""

    public_url: str
    download_backend: str
    download_backend_url: str
    download_access: str
    download_proxy_auth_file: Path
    download_proxy_ca_file: str
    tls_cert_file: str
    tls_key_file: str


def load_web_settings(env: Mapping[str, str] | None = None, *, config_file: Path | None = None) -> WebSettings:
    """Resolve and validate only what the web container uses, so a bad generator-only setting cannot stop web; the same values and messages as load_settings."""
    r = make_resolver(env, config_file=config_file)
    r.mode = _choice(r, 'MODE')
    public_url = r.raw('PUBLIC_URL').rstrip('/')
    if not public_url:
        msg = 'PUBLIC_URL must be set'
        raise ConfigError(msg)
    _check_credential_free_url(r, 'PUBLIC_URL', public_url)
    backend = _choice(r, 'DOWNLOAD_BACKEND')
    backend_url = _explicit_backend_url(r) or _default_backend_url(r, backend)
    return WebSettings(
        public_url=public_url,
        download_backend=backend,
        download_backend_url=backend_url.rstrip('/'),
        download_access=_choice(r, 'DOWNLOAD_ACCESS'),
        download_proxy_auth_file=_secret_path(r, 'DOWNLOAD_PROXY_AUTH_FILE', 'download_proxy_auth'),
        download_proxy_ca_file=r.raw('DOWNLOAD_PROXY_CA_FILE'),
        tls_cert_file=r.raw('TLS_CERT_FILE'),
        tls_key_file=r.raw('TLS_KEY_FILE'),
    )


def apply_process_env(settings: Settings, environ: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """Export the JVM options and proxies uc.toml sets to environ (default: os.environ) where it leaves them unset, so the generator and urllib use them; return the names exported."""
    environ = os.environ if environ is None else environ
    exported = []
    for name in PROCESS_ENV:
        value = getattr(settings, name.lower())
        if value and not environ.get(name, '').strip():
            environ[name] = value
            exported.append(name)
    return tuple(exported)


def _repository(r: Resolver, *, online: bool) -> tuple[str, str, str]:
    """Return the validated (REPO_TYPE, REPO_URL with a trailing slash, REPO_KEY), for load_settings and load_web_settings alike."""
    repo_type = _choice(r, 'REPO_TYPE')
    public_repo = online and repo_type == 'artifactory'
    repo_url = r.raw('REPO_URL') or (PUBLIC_ARTIFACTORY_URL if public_repo else '')
    repo_key = r.raw('REPO_KEY') or ('releases' if public_repo else '')
    if not repo_url or not repo_key:
        msg = 'REPO_URL and REPO_KEY must be set (they default to repo.jenkins-ci.org only in online mode with REPO_TYPE=artifactory)'
        raise ConfigError(msg)
    if not _REPOSITORY_RE.match(repo_key):
        msg = f'{r.label("REPO_KEY")} contains unsupported characters: {repo_key!r}'
        raise ConfigError(msg)
    _check_credential_free_url(r, 'REPO_URL', repo_url, advice='; use the secret files instead')
    return repo_type, with_trailing_slash(repo_url), repo_key


def _secret_path(r: Resolver, name: str, default_file: str) -> Path:
    """Return the secret file name points at, else default_file in SECRETS_DIR."""
    return Path(r.raw(name) or str(Path(r.raw('SECRETS_DIR')) / default_file))


def _default_backend_url(r: Resolver, backend: str, repository: tuple[str, str, str] | None = None) -> str:
    """Return where /download/ requests go when DOWNLOAD_BACKEND_URL is not set.

    repository is the (REPO_TYPE, REPO_URL, REPO_KEY) _repository already validated; without it the repository is
    read here, and only when the backend needs it (the mirror does not).
    """
    if backend == 'mirror':
        return UPSTREAM_MIRROR_URL
    repo_type, repo_url, repo_key = repository or _repository(r, online=r.mode == 'online')
    if repo_type == 'artifact-keeper':
        return f'{repo_url.rstrip("/")}/maven/{repo_key}'
    return repo_url + repo_key


def settings_from(r: Resolver) -> Settings:
    """Build Settings from r's values, applying the mode's defaults; raise ConfigError on bad values."""
    mode = _choice(r, 'MODE')
    r.mode = mode
    online = mode == 'online'

    public_url = r.raw('PUBLIC_URL').rstrip('/')
    if not public_url:
        msg = 'PUBLIC_URL must be set'
        raise ConfigError(msg)
    _check_credential_free_url(r, 'PUBLIC_URL', public_url)

    repo_type, repo_url, repo_key = _repository(r, online=online)
    repo_url_with_port = with_explicit_port(repo_url)
    api_url = with_trailing_slash(_checked_url(r, 'REPO_API_URL') or repo_url_with_port + 'api/')

    repo_adapter = _bool(r, 'REPO_ADAPTER', default=not online or repo_type == 'artifact-keeper')
    if repo_type == 'artifact-keeper' and not repo_adapter:
        msg = 'REPO_TYPE=artifact-keeper needs REPO_ADAPTER=on'
        raise ConfigError(msg)
    repo_ca = r.raw('REPO_CA_FILE')

    backend = _choice(r, 'DOWNLOAD_BACKEND')
    backend_url = (_explicit_backend_url(r) or _default_backend_url(r, backend, (repo_type, repo_url, repo_key))).rstrip('/')
    data_dir = Path(r.raw('UC_DATA_DIR'))

    resources_source = _choice(r, 'RESOURCES_SOURCE')
    resources_url = r.raw('RESOURCES_URL')
    if resources_source == 'url' and not resources_url:
        msg = 'RESOURCES_URL must be set when RESOURCES_SOURCE=url'
        raise ConfigError(msg)
    resources_url = _checked_url(r, 'RESOURCES_URL')

    procurement_source = r.raw('PROCUREMENT_SOURCE_URL')
    _check_credential_free_url(r, 'PROCUREMENT_SOURCE_URL', procurement_source)

    secrets_dir = Path(r.raw('SECRETS_DIR'))

    return Settings(
        mode=mode,
        public_url=public_url,
        uc_id=r.raw('UC_ID'),
        generate_schedule=r.raw('GENERATE_SCHEDULE'),
        doctor_schedule=r.raw('DOCTOR_SCHEDULE'),
        doctor_fetch=_bool(r, 'DOCTOR_FETCH'),
        doctor_since=_version(r, 'DOCTOR_SINCE'),
        doctor_json=_bool(r, 'DOCTOR_JSON'),
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
        repo_adapter_url=with_trailing_slash(_checked_url(r, 'REPO_ADAPTER_URL') or f'http://repo-adapter:{_int(r, "REPO_ADAPTER_PORT")}/artifactory/'),
        repo_adapter_dir=Path(r.raw('REPO_ADAPTER_DIR') or str(data_dir / 'repo-adapter')),
        repo_adapter_port=_int(r, 'REPO_ADAPTER_PORT'),
        repo_ca_file=Path(repo_ca) if repo_ca else None,
        repo_max_archive_mb=_int(r, 'REPO_MAX_ARCHIVE_MB'),
        core_age_days=_int(r, 'CORE_AGE_DAYS'),
        download_backend=backend,
        download_backend_url=backend_url,
        download_access=_choice(r, 'DOWNLOAD_ACCESS'),
        download_proxy_auth_file=_secret_path(r, 'DOWNLOAD_PROXY_AUTH_FILE', 'download_proxy_auth'),
        download_proxy_ca_file=r.raw('DOWNLOAD_PROXY_CA_FILE'),
        procurement_source_url=with_trailing_slash(procurement_source or PUBLIC_RELEASES_URL),
        procurement_source_set=bool(procurement_source),
        retain_releases=_int(r, 'RETAIN_RELEASES'),
        min_plugins=_int(r, 'MIN_PLUGINS'),
        min_plugin_ratio=_ratio(r, 'MIN_PLUGIN_RATIO'),
        certificate_minimum_valid_days=_int(r, 'CERTIFICATE_MINIMUM_VALID_DAYS'),
        pki_org=r.raw('PKI_ORG'),
        pki_root_days=_int(r, 'PKI_ROOT_DAYS'),
        pki_signing_days=_int(r, 'PKI_SIGNING_DAYS'),
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
        overlay_dir=Path(r.raw('UC_OVERLAY_DIR')),
        app_dir=Path(r.raw('UC_APP_DIR')),
        secrets_dir=secrets_dir,
        config_file=r.config_file,
        signing_key_file=_secret_path(r, 'SIGNING_KEY_FILE', 'update-center.key'),
        signing_cert_file=_secret_path(r, 'SIGNING_CERT_FILE', 'update-center.cert'),
        root_ca_file=_secret_path(r, 'ROOT_CA_FILE', 'root-ca.crt'),
        repo_username_file=_secret_path(r, 'REPO_USERNAME_FILE', 'repo_username'),
        repo_password_file=_secret_path(r, 'REPO_PASSWORD_FILE', 'repo_password'),
        github_username_file=_secret_path(r, 'GITHUB_USERNAME_FILE', 'github_username'),
        github_token_file=_secret_path(r, 'GITHUB_TOKEN_FILE', 'github_token'),
        repo_push_auth_file=_secret_path(r, 'REPO_PUSH_AUTH_FILE', 'repo_push_auth'),
    )


def read_config_file(path: Path) -> dict[str, str]:
    """Return the settings the uc.toml at path sets, by variable name and spelled as the environment would; {} when there is no file."""
    try:
        text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return {}
    except IsADirectoryError:
        msg = f'{path} is a directory, not a settings file: remove it, or point UC_CONFIG_FILE at the file'
        raise ConfigError(msg) from None
    except PermissionError:
        msg = f'{path} is not readable by uid {os.getuid()}: make it readable by everyone (chmod 644)'
        raise ConfigError(msg) from None
    except UnicodeDecodeError:
        msg = f'{path} is not UTF-8 text: save it as UTF-8'
        raise ConfigError(msg) from None
    except OSError as exc:
        msg = f'{path} cannot be read: {exc.strerror or exc}'
        raise ConfigError(msg) from None
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = f'{path} is not valid TOML: {exc}'
        raise ConfigError(msg) from None
    values: dict[str, str] = {}
    for name, value in document.items():
        if isinstance(value, dict):
            if name not in SECTIONS:
                raise ConfigError(_unknown(path, f'[{name}]', _FILE_SECTIONS))
            for key, item in value.items():
                _add(values, path, name, key, item)
        else:
            _add(values, path, None, name, value)
    return values


def _add(values: dict[str, str], path: Path, section: str | None, key: str, value: object) -> None:
    label = f'[{section}] {key}' if section else key
    name = env_name(section, key)
    setting = REGISTRY.get(name) if key.islower() else None
    if setting is None:
        raise ConfigError(_unknown(path, label, _FILE_KEYS))
    if setting.env_only:
        msg = f'{label} in {path}: {name} can only be set in the environment (.env), not in this file'
        raise ConfigError(msg)
    if toml_key(name) != (section, key):
        msg = f'{label} in {path}: write {name} as {toml_label(name)}'
        raise ConfigError(msg)
    text = _file_value(setting, value, f'{label} in {path}')
    if text or setting.empty_means_off:
        values[name] = text


def _unknown(path: Path, label: str, candidates: tuple[str, ...]) -> str:
    close = difflib.get_close_matches(label.lower(), candidates, n=1)
    hint = f'; did you mean {close[0]}?' if close else ''
    return f'unknown setting {label} in {path}{hint}'


def _file_value(setting: Setting, value: object, where: str) -> str:
    """Return a uc.toml value spelled as the environment would; a string is taken as the environment would take it."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        if setting.type == 'bool':
            return 'true' if value else 'false'
    elif (isinstance(value, int) and setting.type in {'int', 'ratio'}) or (isinstance(value, float) and setting.type == 'ratio'):
        return str(value)
    expected = {'bool': 'true or false', 'int': 'an integer', 'ratio': 'a number'}.get(setting.type, 'a string')
    msg = f'{where} must be {expected} (got {value!r})'
    raise ConfigError(msg)


def _choice(r: Resolver, name: str) -> str:
    choices = REGISTRY[name].choices
    value = r.raw(name)
    if value not in choices:
        msg = f'{r.label(name)} must be one of {", ".join(choices)} (got {value!r})'
        raise ConfigError(msg)
    return value


def _explicit_backend_url(r: Resolver) -> str:
    """Return DOWNLOAD_BACKEND_URL as set ('' if unset); an '@' in it, and so any credentials, is refused, since web logs it and sends it to controllers."""
    value = r.raw('DOWNLOAD_BACKEND_URL')
    if value:
        _check_credential_free_url(r, 'DOWNLOAD_BACKEND_URL', value, advice='; for proxied downloads put them in secrets/download_proxy_auth')
    return value


def _checked_url(r: Resolver, name: str) -> str:
    """Return name's value, '' if unset; a non-empty value must parse as a URL."""
    value = r.raw(name)
    if value:
        _parse_url(r, name, value)
    return value


def _checked_proxy(r: Resolver, name: str) -> str:
    """Return the proxy URL name sets, '' if unset; only its scheme, host and port are parsed, after the user info, which may hold anything (split_userinfo).

    A value urllib's proxy handler refuses as having no authority (http:/proxy:3128, or svc:/password@proxy:3128 without
    a scheme) is refused too, with a message that quotes none of it.
    """
    value = r.raw(name)
    if value:
        hint = '; percent-encode # ? / [ ] @ in a user name or password'
        if _NO_AUTHORITY.match(value):
            msg = f'{r.label(name)} is not a valid URL: write it as scheme://host:port{hint}'
            raise ConfigError(msg)
        scheme, _, rest = split_userinfo(value)
        _parse_url(r, name, (scheme or '//') + rest, hint=hint)
    return value


def split_userinfo(value: str) -> tuple[str, str | None, str]:
    """Split a URL as urllib's proxy handler reads it: (its scheme and '://', '' without one; the user info before the last '@', None without an '@'; the rest, from the host on).

    The user info may hold an unencoded # ? / that urlsplit takes as the end of the host, and without a scheme the whole
    value is user info and host (user:password@proxy:3128). urllib ends the user info at the authority's last '@'; taking
    it to the value's last '@' reads the same wherever the '@'s are all in the user info, and never leaves part of a
    password in the rest.
    """
    match = _SCHEME.match(value)
    scheme = match.group() if match else ''
    userinfo, at, rest = value[len(scheme) :].rpartition('@')
    return scheme, userinfo if at else None, rest


def _check_credential_free_url(r: Resolver, name: str, value: str, *, advice: str = '') -> None:
    """Refuse any '@' in a URL setting that never carries credentials, then parse it (_parse_url).

    urlsplit ends the user info at a # ? or /, so it would read a token followed by one as the host and accept it;
    refusing the '@' first also keeps any later message from quoting the token. advice follows the message.
    """
    if '@' in value:
        msg = f'{r.label(name)} must not contain credentials or an @{advice}'
        raise ConfigError(msg)
    _parse_url(r, name, value)


def _parse_url(r: Resolver, name: str, value: str, *, hint: str = '') -> SplitResult:
    """Return value split into its parts; a URL urllib cannot parse, or whose port is not a number, is a ConfigError naming the setting.

    The message never includes urllib's reason, which can quote part of the value: a password, say.
    """
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError for a port that is not a number in range
    except ValueError:
        msg = f'{r.label(name)} is not a valid URL: its host or port cannot be read{hint}'
        raise ConfigError(msg) from None
    return parts


def _version(r: Resolver, name: str) -> str:
    raw = r.raw(name)
    if raw and not CORE_VERSION.match(raw):
        msg = f'{r.label(name)} must be a Jenkins version such as 2.462.1 (got {raw!r})'
        raise ConfigError(msg)
    return raw


def _int(r: Resolver, name: str) -> int:
    raw = r.raw(name)
    try:
        value = int(raw)
    except ValueError:
        msg = f'{r.label(name)} must be an integer (got {raw!r})'
        raise ConfigError(msg) from None
    minimum = REGISTRY[name].minimum or 0
    if value < minimum:
        msg = f'{r.label(name)} must be >= {minimum} (got {value})'
        raise ConfigError(msg)
    return value


def _ratio(r: Resolver, name: str) -> float:
    raw = r.raw(name)
    try:
        value = float(raw)
    except ValueError:
        msg = f'{r.label(name)} must be a number (got {raw!r})'
        raise ConfigError(msg) from None
    if not 0 <= value <= 1:
        msg = f'{r.label(name)} must be between 0 and 1 (got {value})'
        raise ConfigError(msg)
    return value


def _bool(r: Resolver, name: str, *, default: bool = False) -> bool:
    raw = (r.raw(name) or ('true' if default else 'false')).lower()
    if raw in {'1', 'true', 'yes', 'on'}:
        return True
    if raw in {'0', 'false', 'no', 'off'}:
        return False
    msg = f'{r.label(name)} must be true or false (got {raw!r})'
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


def settings_digest(settings: Settings, environ: Mapping[str, str]) -> str:
    """Return a fingerprint of what a running service keeps from when it started: the effective values of the restart settings, and every registry setting in environ.

    Each environment value is taken as Resolver.raw takes it: stripped, and absent when blank, so an empty `RETAIN_RELEASES=` in .env
    changes nothing; a blank POPULARITY_JSON_URL or TOOL_INSTALLERS_URL is a real value (off) and stays.
    environ is the environment the container received (its env_file), which is fixed when the container is created, so
    a change in .env needs a recreate whatever the setting; a change in uc.toml matters only for the restart settings.
    Pass a snapshot taken before apply_process_env exports anything from uc.toml. The scheduler records the digest, and
    uc config compares it to tell whether the running services use the current settings.
    """
    marked = {setting.env: getattr(settings, setting.attribute) for setting in SETTINGS if setting.restart and setting.attribute}
    given = {}
    for name, setting in REGISTRY.items():
        value = environ.get(name, '').strip()
        if name in environ and (value or setting.empty_means_off):
            given[name] = value
    text = json.dumps({'effective': marked, 'environment': given}, default=str, sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
