"""Every setting uc, the web container and Compose read, defined once: its environment variable, its place in uc.toml, its type and its default.

docs/reference/configuration.md documents each one, and tools/check_settings_documented.py keeps the two in step by
reading the entries of SETTINGS below. A setting's place in uc.toml follows from its name
(docs/reference/configuration.md#how-settings-are-read): a variable whose name starts with a section's name and an
underscore lives in that section, under the rest of its name in lower case; every other variable is a top-level key
in lower case.
"""

from __future__ import annotations

from dataclasses import dataclass

SECTIONS = ('generate', 'doctor', 'repo', 'download', 'resources', 'tls', 'procurement', 'pki', 'signing', 'github', 'uc')
TYPES = ('str', 'bool', 'int', 'ratio', 'choice', 'cron', 'path', 'url')
GROUPS = (
    'Required',
    'Source repository',
    'repo-adapter',
    'Downloads',
    'Upstream data',
    'Generation',
    'Signing',
    'Web and TLS',
    'Procurement',
    'Testing and evaluation',
    'Read by bundled software',
    'Internal',
    'Compose',
)
"""The tables of docs/reference/configuration.md, in page order; uc config and the example files group settings the same way."""

MODES = ('online', 'airgapped')
REPO_TYPES = ('artifactory', 'artifact-keeper')
BACKENDS = ('mirror', 'artifactory')
ACCESS_MODES = ('redirect', 'proxy')
RESOURCE_SOURCES = ('github', 'url', 'bundled')
UPSTREAM_POPULARITY_URL = 'https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/plugin-installation-trend/latestNumbers.json'
UPSTREAM_TOOL_INSTALLERS_URL = 'https://updates.jenkins.io/updates/'
DEFAULT_ADAPTER_URL = 'http://repo-adapter:8081/artifactory/'


def toml_key(env: str) -> tuple[str | None, str]:
    """Return the section (None at the top level) and the key that hold env in uc.toml."""
    for section in SECTIONS:
        prefix = section.upper() + '_'
        if env.startswith(prefix):
            return section, env.removeprefix(prefix).lower()
    return None, env.lower()


def env_name(section: str | None, key: str) -> str:
    """Return the environment variable for key in section (None: the top level) of uc.toml."""
    return f'{section}_{key}'.upper() if section else key.upper()


def toml_label(env: str) -> str:
    """Return how env is written in uc.toml, for messages and tables: `[repo] url`, or `public_url` at the top level."""
    section, key = toml_key(env)
    return f'[{section}] {key}' if section else key


@dataclass(frozen=True)
class Setting:
    """One setting: its name, type and default, and how uc config and the example files show it.

    Defaults are spelled as the environment would spell them. A derived setting has none here: config.py works it
    out from other settings (REPO_URL from the mode and REPO_TYPE, the secret paths from SECRETS_DIR).
    """

    env: str
    type: str
    group: str
    doc: str
    default: str = ''
    online: str | None = None  # the default in online mode, when it differs
    airgapped: str | None = None  # the default in air-gapped mode, when it differs
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    empty_means_off: bool = False  # an explicitly empty value turns the feature off instead of meaning "not set"
    env_only: bool = False  # read before uc.toml exists (by Compose, or to find uc.toml): environment only
    secret_file: bool = False  # the path of a secret file; uc config shows whether it exists, never its contents
    derived: bool = False
    attr: str | None = None  # the Settings field, when it is not env in lower case
    hint: tuple[str, ...] = ()  # extra comment lines in the example files
    sample: str = ''  # the value the example files show when the default is empty
    active: tuple[str, ...] = ()  # the modes whose example files set it, uncommented

    @property
    def attribute(self) -> str | None:
        """Return the Settings field holding this setting's value; None for the settings only Compose or the environment read."""
        if self.env_only:
            return None
        return self.attr or self.env.lower()

    def default_for(self, mode: str) -> str:
        """Return the default for mode as the environment would spell it; '' when there is none or the code derives it."""
        if mode == 'online' and self.online is not None:
            return self.online
        if mode == 'airgapped' and self.airgapped is not None:
            return self.airgapped
        return self.default


SETTINGS: tuple[Setting, ...] = (
    # Required
    Setting('MODE', 'choice', 'Required', 'online or airgapped. It only sets defaults; every other setting can still be set explicitly.', choices=MODES, active=MODES),
    Setting('PUBLIC_URL', 'url', 'Required', 'The exact origin Jenkins uses, with no path.', sample='https://jenkins-updates.example.com', active=MODES),
    # Source repository
    Setting('REPO_TYPE', 'choice', 'Source repository', 'artifactory or artifact-keeper. Artifact Keeper is read only through the repo-adapter.', default='artifactory', choices=REPO_TYPES),
    Setting(
        'REPO_URL',
        'url',
        'Source repository',
        "Artifactory: the /artifactory/ base URL; Artifact Keeper: the server's base URL. It defaults to https://repo.jenkins-ci.org/ only online with REPO_TYPE=artifactory.",
        derived=True,
        sample='https://artifactory.corp.example/artifactory/',
        active=('airgapped',),
    ),
    Setting('REPO_API_URL', 'url', 'Source repository', "Artifactory's API base for the listing query, ${REPO_URL}api/ by default. Set it only when the API is served elsewhere.", derived=True),
    Setting('REPO_KEY', 'str', 'Source repository', 'The repository key (for Artifact Keeper, the key, not the display name). It defaults to releases only online with REPO_TYPE=artifactory.', derived=True, sample='jenkins-plugins-local', active=('airgapped',)),
    # repo-adapter
    Setting('REPO_ADAPTER', 'bool', 'repo-adapter', 'Read the repository through the repo-adapter: on by default air-gapped and always with Artifact Keeper, off otherwise.', derived=True, sample='true'),
    Setting('REPO_ADAPTER_URL', 'url', 'repo-adapter', 'Where uc finds the repo-adapter. Change it only with a non-default Compose layout.', default=DEFAULT_ADAPTER_URL),
    Setting('REPO_ADAPTER_DIR', 'path', 'repo-adapter', "The repo-adapter's token, cache and report.json, ${UC_DATA_DIR}/repo-adapter by default.", derived=True),
    Setting('REPO_CA_FILE', 'path', 'repo-adapter', 'A PEM bundle added to the system bundle, for a repository behind a private CA.', sample='/etc/ssl/uc-repo-ca.crt'),
    Setting('REPO_MAX_ARCHIVE_MB', 'int', 'repo-adapter', 'Archives larger than this are skipped and reported.', default='512', minimum=1),
    # Downloads
    Setting('DOWNLOAD_BACKEND', 'choice', 'Downloads', 'mirror (the get.jenkins.io layout) or artifactory (any Maven-layout repository).', online='mirror', airgapped='artifactory', choices=BACKENDS),
    Setting(
        'DOWNLOAD_BACKEND_URL',
        'url',
        'Downloads',
        'Where /download/ requests go: https://get.jenkins.io for mirror, the repository (${REPO_URL}${REPO_KEY}; Artifact Keeper ${REPO_URL}maven/${REPO_KEY}) for artifactory.',
        derived=True,
    ),
    Setting(
        'DOWNLOAD_ACCESS',
        'choice',
        'Downloads',
        'redirect sends controllers to the backend; proxy streams each file through the web container.',
        default='redirect',
        choices=ACCESS_MODES,
        hint=('Controllers cannot read the repository anonymously? Use proxy: docs/how-to/proxy-downloads.md',),
    ),
    Setting('DOWNLOAD_PROXY_CA_FILE', 'path', 'Downloads', "Proxy mode only: the CA bundle the backend's certificate must verify against.", default='/etc/ssl/certs/ca-certificates.crt'),
    Setting('DOWNLOAD_PROXY_AUTH_FILE', 'path', 'Downloads', 'Proxy mode only: the file holding username:password for the backend, ${SECRETS_DIR}/download_proxy_auth by default.', derived=True, secret_file=True),
    # Upstream data
    Setting(
        'RESOURCES_SOURCE',
        'choice',
        'Upstream data',
        'Where security warnings, suspensions and labels come from: github (upstream at RESOURCES_REF), url (a tarball at RESOURCES_URL) or bundled (the copy in the image).',
        online='github',
        airgapped='bundled',
        choices=RESOURCE_SOURCES,
    ),
    Setting('RESOURCES_REF', 'str', 'Upstream data', 'The update-center2 branch, tag or commit to read with RESOURCES_SOURCE=github.', default='master'),
    Setting('RESOURCES_URL', 'url', 'Upstream data', 'Required with RESOURCES_SOURCE=url: a tarball containing resources/.', sample='https://artifactory.corp.example/artifactory/generic-local/update-center2-master.tar.gz'),
    Setting('RESOURCES_SHA256', 'str', 'Upstream data', 'With RESOURCES_SOURCE=url, the tarball must match this checksum.'),
    Setting('POPULARITY_JSON_URL', 'url', 'Upstream data', 'Plugin install counts. Set it empty to skip them; popularity is then 0.', online=UPSTREAM_POPULARITY_URL, airgapped='', empty_means_off=True),
    Setting('TOOL_INSTALLERS_URL', 'url', 'Upstream data', 'A directory listing of the updates/ tool-installer files. Set it empty to skip them.', online=UPSTREAM_TOOL_INSTALLERS_URL, airgapped='', empty_means_off=True),
    Setting('STALE_AFTER_DAYS', 'int', 'Upstream data', 'status.json marks the resources stale when they are older than this.', default='30'),
    # Generation
    Setting('UC_ID', 'str', 'Generation', 'The update site id written into the metadata; it must match the update site id on your controllers.', default='default'),
    Setting('GENERATE_SCHEDULE', 'cron', 'Generation', 'Cron expression for regular generation. The scheduler also generates at once when no release is live.', default='0 */6 * * *'),
    Setting('CORE_AGE_DAYS', 'int', 'Generation', 'Tiers are offered for cores released within this many days; 0 means every core a plugin requires.', online='400', airgapped='0'),
    Setting('RETAIN_RELEASES', 'int', 'Generation', 'Releases kept for rollback.', default='5', minimum=1),
    Setting('MIN_PLUGINS', 'int', 'Generation', 'A release with fewer plugins is refused.', default='1'),
    Setting('MIN_PLUGIN_RATIO', 'ratio', 'Generation', "A release with fewer than this share of the live release's plugins is refused, unless run with uc generate --force.", default='0.9'),
    # Signing
    Setting('CERTIFICATE_MINIMUM_VALID_DAYS', 'int', 'Signing', 'Generation stops when the signing certificate expires within this many days.', default='30'),
    Setting('SIGNING_CERT_DAYS', 'int', 'Signing', 'Read and validated, but it has no effect today: uc pki sets the lifetime.', default='365', minimum=1),
    # Web and TLS
    Setting('TLS_CERT_FILE', 'path', 'Web and TLS', 'With TLS_KEY_FILE, enables HTTPS on port 8443: a path inside the web container.'),
    Setting('TLS_KEY_FILE', 'path', 'Web and TLS', 'The private key for TLS_CERT_FILE.'),
    # Procurement
    Setting(
        'PROCUREMENT_SOURCE_URL',
        'url',
        'Procurement',
        'Where uc doctor --download-* fetches files from, by Maven path; https://repo.jenkins-ci.org/releases/ by default. Air-gapped, downloads are refused unless it is set.',
        derived=True,
    ),
    # Testing and evaluation
    Setting('ALLOW_LOCAL_PUBLIC_URL', 'bool', 'Testing and evaluation', 'Allows a localhost or loopback PUBLIC_URL, which controllers on other hosts cannot reach. Not for production.', default='false'),
    Setting('UC_EXTRA_ARGS', 'str', 'Testing and evaluation', 'Extra update-center2 arguments appended to every run, for example --max-plugins 25 to cap a trial run.', attr='extra_args'),
    # Read by bundled software
    Setting(
        'JAVA_TOOL_OPTIONS',
        'str',
        'Read by bundled software',
        "The generator's JVM options: heap size (-Xmx6g) and outbound proxy (-Dhttps.proxyHost=... -Dhttps.proxyPort=...).",
        sample='-Xmx4g -Dhttps.proxyHost=proxy.example.com -Dhttps.proxyPort=3128',
    ),
    Setting(
        'HTTPS_PROXY',
        'url',
        'Read by bundled software',
        'The outbound proxy for everything the generator does not fetch itself. Behind a proxy, set it and the proxy flags in JAVA_TOOL_OPTIONS.',
        sample='http://proxy.example.com:3128',
    ),
    Setting('HTTP_PROXY', 'url', 'Read by bundled software', 'As HTTPS_PROXY, for plain-http URLs.'),
    Setting('NO_PROXY', 'str', 'Read by bundled software', 'Hosts to reach directly. With a proxy set, include repo-adapter (and an internal repository host).', sample='repo-adapter'),
    Setting(
        'COMPOSE_FILE',
        'str',
        'Read by bundled software',
        'The Compose files every plain docker compose command uses (on Windows, separate them with ;).',
        airgapped='compose.yaml:compose.airgapped.yaml',
        env_only=True,
        hint=('Air-gapped: uncomment this, so plain docker compose uses the air-gapped override.',),
    ),
    # Internal
    Setting('UC_DATA_DIR', 'path', 'Internal', 'The data volume.', default='/data', attr='data_dir'),
    Setting('UC_OVERLAY_DIR', 'path', 'Internal', 'The overlay directory, config/overlay/ on the host.', default='/config/overlay', attr='overlay_dir'),
    Setting('UC_APP_DIR', 'path', 'Internal', 'The generator, templates and bundled resources in the image.', default='/opt/uc', attr='app_dir'),
    Setting('SECRETS_DIR', 'path', 'Internal', 'Where the secret files are read from.', default='/run/secrets'),
    Setting('SIGNING_KEY_FILE', 'path', 'Internal', 'The signing key, ${SECRETS_DIR}/update-center.key by default.', derived=True, secret_file=True),
    Setting('SIGNING_CERT_FILE', 'path', 'Internal', 'The signing certificate, ${SECRETS_DIR}/update-center.cert by default.', derived=True, secret_file=True),
    Setting('ROOT_CA_FILE', 'path', 'Internal', 'The root CA certificate, ${SECRETS_DIR}/root-ca.crt by default.', derived=True, secret_file=True),
    Setting('REPO_USERNAME_FILE', 'path', 'Internal', 'The repository account, ${SECRETS_DIR}/repo_username by default.', derived=True, secret_file=True),
    Setting('REPO_PASSWORD_FILE', 'path', 'Internal', "The repository account's password or token, ${SECRETS_DIR}/repo_password by default.", derived=True, secret_file=True),
    Setting('GITHUB_USERNAME_FILE', 'path', 'Internal', 'The GitHub user, ${SECRETS_DIR}/github_username by default.', derived=True, secret_file=True),
    Setting('GITHUB_TOKEN_FILE', 'path', 'Internal', 'The GitHub token, ${SECRETS_DIR}/github_token by default.', derived=True, secret_file=True),
    Setting('REPO_PUSH_AUTH_FILE', 'path', 'Internal', 'The account that can write to the repository, ${SECRETS_DIR}/repo_push_auth by default.', derived=True, secret_file=True),
    # Compose
    Setting('UC_ENV_FILE', 'path', 'Compose', 'The file every service loads as env_file.', default='.env', env_only=True),
    Setting('UC_IMAGE', 'str', 'Compose', 'The image for uc, scheduler and repo-adapter.', default='jenkins-uc/uc:local', env_only=True),
    Setting('WEB_IMAGE', 'str', 'Compose', 'The image for web.', default='jenkins-uc/web:local', env_only=True),
    Setting('HTTP_PORT', 'int', 'Compose', "Host port published for the web container's HTTP listener.", default='8080', env_only=True),
    Setting('HTTPS_PORT', 'int', 'Compose', "Host port published for the web container's TLS listener.", default='8443', env_only=True),
)
REGISTRY: dict[str, Setting] = {setting.env: setting for setting in SETTINGS}
