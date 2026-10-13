"""Every setting uc, the web container and Compose read, defined once.

Its environment variable, its type and its default.

docs/reference/configuration.md documents each one, and
tools/check_settings_documented.py keeps the two in step by reading the entries of
SETTINGS below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TYPES = ('str', 'bool', 'int', 'ratio', 'choice', 'cron', 'path', 'url', 'version')
GROUPS = (
    'Required',
    'Source repository',
    'repo-adapter',
    'Downloads',
    'Upstream data',
    'Generation',
    'Doctor',
    'Signing',
    'Web and TLS',
    'Procurement',
    'Testing and evaluation',
    'Read by bundled software',
    'Compose',
)
"""The tables of docs/reference/configuration.md, in page order.

uc config and the example files group settings the same way.
"""

MODES = ('online', 'airgapped')
REPO_TYPES = ('artifactory', 'artifact-keeper')
BACKENDS = ('mirror', 'artifactory')
ACCESS_MODES = ('redirect', 'proxy')
RESOURCE_SOURCES = ('github', 'url', 'bundled')
UPSTREAM_POPULARITY_URL = 'https://raw.githubusercontent.com/jenkins-infra/infra-statistics/gh-pages/plugin-installation-trend/latestNumbers.json'
UPSTREAM_TOOL_INSTALLERS_URL = 'https://updates.jenkins.io/updates/'
CORE_VERSION = re.compile(r'^[0-9]+\.[0-9]+(\.[0-9]+)?$')
"""A Jenkins core version such as 2.462 or 2.462.1.

The pattern of the `version` setting type, DOCTOR_SINCE and uc doctor's version
arguments.
"""


@dataclass(frozen=True)
class Setting:
    """One setting: its name, type and default.

    Also how uc config and the example files show it.

    Defaults are spelled as the environment would spell them. A derived setting has none
    here: config.py works it out from other settings (REPO_URL from the mode and
    REPO_TYPE, the secret paths from SECRETS_DIR).
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
    # an explicitly empty value turns the feature off instead of meaning "not set"
    empty_means_off: bool = False
    # read by Compose or the shell, not by uc: it has no Settings field
    env_only: bool = False
    derived: bool = False
    attr: str | None = None  # the Settings field, when it is not env in lower case
    # extra comment lines in the example files, each a sentence ending in a period
    hint: tuple[str, ...] = ()
    sample: str = ''  # the value the example files show when the default is empty
    # more comment lines, in the online example only (after hint)
    online_hint: tuple[str, ...] = ()
    # more comment lines, in the air-gapped example only (after hint)
    airgapped_hint: tuple[str, ...] = ()
    # the value the online example shows, ahead of sample and the default
    online_sample: str = ''
    # the value the air-gapped example shows, ahead of sample and the default
    airgapped_sample: str = ''
    active: tuple[str, ...] = ()  # the modes whose example files set it, uncommented

    @property
    def attribute(self) -> str | None:
        """Return the Settings field holding this setting's value.

        None for the settings only Compose or the environment read.
        """
        if self.env_only:
            return None
        return self.attr or self.env.lower()

    def hint_for(self, mode: str) -> tuple[str, ...]:
        """Return the comment lines the example for mode shows.

        The common hint, then the mode's own.
        """
        return self.hint + (
            self.online_hint if mode == 'online' else self.airgapped_hint
        )

    def sample_for(self, mode: str) -> str:
        """Return the example value for mode.

        The mode's own sample, else the common one; '' when there is none.
        """
        return (
            self.online_sample if mode == 'online' else self.airgapped_sample
        ) or self.sample

    def default_for(self, mode: str) -> str:
        """Return the default for mode as the environment would spell it.

        '' when there is none or the code derives it.
        """
        if mode == 'online' and self.online is not None:
            return self.online
        if mode == 'airgapped' and self.airgapped is not None:
            return self.airgapped
        return self.default


SETTINGS: tuple[Setting, ...] = (
    # Required
    Setting(
        'MODE',
        'choice',
        'Required',
        (
            'online or airgapped. It only sets defaults; every other setting can still '
            'be set explicitly.'
        ),
        choices=MODES,
        active=MODES,
    ),
    Setting(
        'PUBLIC_URL',
        'url',
        'Required',
        'The exact origin Jenkins uses, with no path.',
        sample='https://jenkins-updates.example.com',
        active=MODES,
    ),
    # Source repository
    Setting(
        'REPO_TYPE',
        'choice',
        'Source repository',
        (
            'artifactory or artifact-keeper. Artifact Keeper is read only through the '
            'repo-adapter.'
        ),
        default='artifactory',
        choices=REPO_TYPES,
        online_hint=(
            (
                'Your own Artifactory or Artifact Keeper instead of '
                'repo.jenkins-ci.org? Set REPO_TYPE, REPO_URL and REPO_KEY here.'
            ),
            (
                'Also set DOWNLOAD_BACKEND=artifactory, and usually CORE_AGE_DAYS=0. '
                'See docs/architecture/modes.md#your-own-repository.'
            ),
        ),
    ),
    Setting(
        'REPO_URL',
        'url',
        'Source repository',
        (
            "Artifactory: the /artifactory/ base URL; Artifact Keeper: the server's "
            'base URL. It defaults to https://repo.jenkins-ci.org/ only online with '
            'REPO_TYPE=artifactory.'
        ),
        derived=True,
        sample='https://artifactory.corp.example/artifactory/',
        active=('airgapped',),
    ),
    Setting(
        'REPO_API_URL',
        'url',
        'Source repository',
        (
            "Artifactory's API base for the listing query, ${REPO_URL}api/ by default. "
            'Set it only when the API is served elsewhere.'
        ),
        derived=True,
    ),
    Setting(
        'REPO_KEY',
        'str',
        'Source repository',
        (
            'The repository key (for Artifact Keeper, the key, not the display name). '
            'It defaults to releases only online with REPO_TYPE=artifactory.'
        ),
        derived=True,
        sample='jenkins-plugins-local',
        active=('airgapped',),
    ),
    # repo-adapter
    Setting(
        'REPO_ADAPTER',
        'bool',
        'repo-adapter',
        (
            'Read the repository through the repo-adapter: on by default air-gapped '
            'and always with Artifact Keeper, off otherwise.'
        ),
        derived=True,
        sample='true',
    ),
    Setting(
        'REPO_CA_FILE',
        'path',
        'repo-adapter',
        (
            'A PEM bundle added to the system bundle, for a repository behind a '
            'private CA.'
        ),
        sample='/etc/ssl/uc-repo-ca.crt',
        hint=(
            (
                'Mount the bundle into the repo-adapter (and uc) and point this at it. '
                'See docs/getting-started/airgapped.md#repository-behind-a-private-ca.'
            ),
        ),
    ),
    Setting(
        'REPO_MAX_ARCHIVE_MB',
        'int',
        'repo-adapter',
        'Archives larger than this are skipped and reported.',
        default='512',
        minimum=1,
    ),
    # Downloads
    Setting(
        'DOWNLOAD_BACKEND',
        'choice',
        'Downloads',
        (
            'mirror (the get.jenkins.io layout) or artifactory (any Maven-layout '
            'repository).'
        ),
        online='mirror',
        airgapped='artifactory',
        choices=BACKENDS,
    ),
    Setting(
        'DOWNLOAD_BACKEND_URL',
        'url',
        'Downloads',
        (
            'Where /download/ requests go: https://get.jenkins.io by default for '
            'mirror; required for artifactory: the repository by Maven path '
            '(Artifactory ${REPO_URL}${REPO_KEY}/, Artifact Keeper '
            '${REPO_URL}maven/${REPO_KEY}/).'
        ),
        derived=True,
        sample='https://artifactory.corp.example/artifactory/jenkins-plugins-local/',
        active=('airgapped',),
        online_hint=(
            (
                'With your own repository and DOWNLOAD_BACKEND=artifactory, set it to '
                'that repository by Maven path.'
            ),
        ),
    ),
    Setting(
        'DOWNLOAD_ACCESS',
        'choice',
        'Downloads',
        (
            'redirect sends controllers to the backend; proxy streams each file '
            'through the web container.'
        ),
        default='redirect',
        choices=ACCESS_MODES,
        hint=(
            (
                'Controllers cannot read the repository anonymously? Use proxy. See '
                'docs/how-to/proxy-downloads.md.'
            ),
        ),
    ),
    Setting(
        'DOWNLOAD_PROXY_CA_FILE',
        'path',
        'Downloads',
        "Proxy mode only: the CA bundle the backend's certificate must verify against.",
        default='/etc/ssl/certs/ca-certificates.crt',
        hint=(
            (
                'Mount the bundle into web and point this at it. See '
                'docs/how-to/proxy-downloads.md#backend-with-a-private-ca.'
            ),
        ),
    ),
    # Upstream data
    Setting(
        'RESOURCES_SOURCE',
        'choice',
        'Upstream data',
        (
            'Where security warnings, suspensions and labels come from: github '
            '(upstream at RESOURCES_REF), url (a tarball at RESOURCES_URL) or bundled '
            '(the copy in the image).'
        ),
        online='github',
        airgapped='bundled',
        choices=RESOURCE_SOURCES,
    ),
    Setting(
        'RESOURCES_REF',
        'str',
        'Upstream data',
        (
            'The update-center2 branch, tag or commit to read with '
            'RESOURCES_SOURCE=github.'
        ),
        default='master',
    ),
    Setting(
        'RESOURCES_URL',
        'url',
        'Upstream data',
        'Required with RESOURCES_SOURCE=url: a tarball containing resources/.',
        sample='https://artifactory.corp.example/artifactory/generic-local/update-center2-master.tar.gz',
    ),
    Setting(
        'RESOURCES_SHA256',
        'str',
        'Upstream data',
        'With RESOURCES_SOURCE=url, the tarball must match this checksum.',
    ),
    Setting(
        'POPULARITY_JSON_URL',
        'url',
        'Upstream data',
        'Plugin install counts. Set it empty to skip them; popularity is then 0.',
        online=UPSTREAM_POPULARITY_URL,
        airgapped='',
        empty_means_off=True,
        airgapped_sample='https://artifactory.corp.example/artifactory/generic-local/latestNumbers.json',
        airgapped_hint=(
            (
                'A copy you procured, to keep popularity. See '
                'docs/how-to/keep-upstream-data-fresh.md.'
            ),
        ),
    ),
    Setting(
        'TOOL_INSTALLERS_URL',
        'url',
        'Upstream data',
        (
            'A directory listing of the updates/ tool-installer files. Set it empty to '
            'skip them.'
        ),
        online=UPSTREAM_TOOL_INSTALLERS_URL,
        airgapped='',
        empty_means_off=True,
        airgapped_sample='https://artifactory.corp.example/artifactory/generic-local/updates/',
        airgapped_hint=(
            (
                'A copy you procured, to keep the tool installers. See '
                'docs/how-to/keep-upstream-data-fresh.md.'
            ),
        ),
    ),
    Setting(
        'STALE_AFTER_DAYS',
        'int',
        'Upstream data',
        'status.json marks the resources stale when they are older than this.',
        default='30',
    ),
    # Generation
    Setting(
        'UC_ID',
        'str',
        'Generation',
        (
            'The update site id written into the metadata; it must match the update '
            'site id on your controllers.'
        ),
        default='default',
    ),
    Setting(
        'GENERATE_SCHEDULE',
        'cron',
        'Generation',
        (
            'Cron expression for regular generation. The scheduler also generates at '
            'once when no release is live.'
        ),
        default='0 */6 * * *',
    ),
    Setting(
        'CORE_AGE_DAYS',
        'int',
        'Generation',
        (
            'Tiers are offered for cores released within this many days; 0 means every '
            'core a plugin requires.'
        ),
        online='400',
        airgapped='0',
        airgapped_hint=(
            (
                '400 behaves like updates.jenkins.io (tiers only for cores released in '
                'the last 400 days).'
            ),
        ),
    ),
    Setting(
        'RETAIN_RELEASES',
        'int',
        'Generation',
        'Releases kept for rollback.',
        default='5',
        minimum=1,
    ),
    Setting(
        'MIN_PLUGIN_RATIO',
        'ratio',
        'Generation',
        (
            "A release with fewer than this share of the live release's plugins is "
            'refused, unless run with uc generate --force.'
        ),
        default='0.9',
    ),
    # Doctor
    Setting(
        'DOCTOR_SCHEDULE',
        'cron',
        'Doctor',
        (
            'Cron expression for a read-only uc doctor --record run, which saves the '
            'report and its counts in status.json; empty means none.'
        ),
        sample='30 5 * * *',
    ),
    Setting(
        'DOCTOR_FETCH',
        'bool',
        'Doctor',
        (
            'uc doctor downloads the POMs and manifests missing from the cache, so it '
            'can check every dependency and required core (--fetch).'
        ),
        default='false',
    ),
    Setting(
        'DOCTOR_SINCE',
        'version',
        'Doctor',
        (
            'The floor for required cores (--since); empty means the oldest core in '
            'the repository.'
        ),
        sample='2.462.1',
    ),
    # Signing
    Setting(
        'CERTIFICATE_MINIMUM_VALID_DAYS',
        'int',
        'Signing',
        'Generation stops when the signing certificate expires within this many days.',
        default='30',
    ),
    # Web and TLS
    Setting(
        'TLS_CERT_FILE',
        'path',
        'Web and TLS',
        (
            'With TLS_KEY_FILE, enables HTTPS on port 8443: a path inside the web '
            'container.'
        ),
    ),
    Setting(
        'TLS_KEY_FILE', 'path', 'Web and TLS', 'The private key for TLS_CERT_FILE.'
    ),
    # Procurement
    Setting(
        'PROCUREMENT_SOURCE_URL',
        'url',
        'Procurement',
        (
            'Where uc procure stage fetches files from, by Maven path; '
            'https://repo.jenkins-ci.org/releases/ by default. Air-gapped, downloads '
            'are refused unless it is set.'
        ),
        derived=True,
    ),
    # Testing and evaluation
    Setting(
        'ALLOW_LOCAL_PUBLIC_URL',
        'bool',
        'Testing and evaluation',
        (
            'Allows a localhost or loopback PUBLIC_URL, which controllers on other '
            'hosts cannot reach. Not for production.'
        ),
        default='false',
    ),
    Setting(
        'UC_EXTRA_ARGS',
        'str',
        'Testing and evaluation',
        (
            'Extra update-center2 arguments appended to every run, for example '
            '--max-plugins 25 to cap a trial run.'
        ),
        attr='extra_args',
    ),
    # Read by bundled software
    Setting(
        'JAVA_TOOL_OPTIONS',
        'str',
        'Read by bundled software',
        (
            "The generator's JVM options: heap size (-Xmx6g) and outbound proxy "
            '(-Dhttps.proxyHost=... -Dhttps.proxyPort=...).'
        ),
        sample='-Xmx4g -Dhttps.proxyHost=proxy.example.com -Dhttps.proxyPort=3128',
    ),
    Setting(
        'HTTPS_PROXY',
        'url',
        'Read by bundled software',
        (
            'The outbound proxy for everything the generator does not fetch itself. '
            'Behind a proxy, set it and the proxy flags in JAVA_TOOL_OPTIONS.'
        ),
        sample='http://proxy.example.com:3128',
    ),
    Setting(
        'HTTP_PROXY',
        'url',
        'Read by bundled software',
        'As HTTPS_PROXY, for plain-http URLs.',
    ),
    Setting(
        'NO_PROXY',
        'str',
        'Read by bundled software',
        (
            'Hosts to reach directly. With a proxy set, include repo-adapter (and an '
            'internal repository host).'
        ),
        sample='repo-adapter',
    ),
    Setting(
        'COMPOSE_FILE',
        'str',
        'Read by bundled software',
        (
            'The Compose files every plain docker compose command uses (on Windows, '
            'separate them with ;).'
        ),
        env_only=True,
        hint=(
            (
                'To run images built from your checkout, add compose.build.yaml '
                '(docs/getting-started/online.md#or-build-from-source).'
            ),
        ),
        airgapped_sample='compose.yaml:compose.airgapped.yaml',
        airgapped_hint=(
            (
                'Its default is compose.yaml alone: uncomment this so plain docker '
                'compose uses the air-gapped override.'
            ),
        ),
    ),
    # Compose
    Setting(
        'UC_ENV_FILE',
        'path',
        'Compose',
        (
            'The file every uc-image service loads as env_file; web takes its '
            "variables from Compose's own .env (or --env-file)."
        ),
        default='.env',
        env_only=True,
    ),
    Setting(
        'UC_UID',
        'int',
        'Compose',
        (
            'The user id every container runs as. On Linux, set it to the output of id '
            '-u, so the files in secrets/ and procurement/ stay yours.'
        ),
        default='10001',
        env_only=True,
    ),
    Setting(
        'UC_GID',
        'int',
        'Compose',
        'The group id every container runs as. On Linux, the output of id -g.',
        default='10001',
        env_only=True,
    ),
    Setting(
        'UC_IMAGE',
        'str',
        'Compose',
        (
            'The image for uc, scheduler, repo-adapter and setup. Unset, this '
            "release's, ghcr.io/haletechco/easy-update-center-uc:<version>, or "
            'easy-update-center/uc:local with compose.build.yaml.'
        ),
        env_only=True,
    ),
    Setting(
        'WEB_IMAGE',
        'str',
        'Compose',
        (
            "The image for web. Unset, this release's, "
            'ghcr.io/haletechco/easy-update-center-web:<version>, or '
            'easy-update-center/web:local with compose.build.yaml.'
        ),
        env_only=True,
    ),
    Setting(
        'HTTP_PORT',
        'int',
        'Compose',
        "Host port published for the web container's HTTP listener.",
        default='8080',
        env_only=True,
    ),
    Setting(
        'HTTPS_PORT',
        'int',
        'Compose',
        "Host port published for the web container's TLS listener.",
        default='8443',
        env_only=True,
    ),
)
REGISTRY: dict[str, Setting] = {setting.env: setting for setting in SETTINGS}
