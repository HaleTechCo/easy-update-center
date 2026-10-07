"""The settings registry: names, the uc.toml naming rule, types, groups and defaults (docs/reference/configuration.md#how-settings-are-read)."""

import re
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from uc import cli, config
from uc.config import Settings
from uc.repo_adapter import server as adapter_server
from uc.settings import CORE_VERSION, GROUPS, MODES, REGISTRY, SETTINGS, TYPES, env_name, toml_key, toml_label


def test_names_are_unique_upper_case_variables():
    names = [setting.env for setting in SETTINGS]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r'[A-Z][A-Z0-9_]*', name) for name in names)
    assert set(REGISTRY) == set(names)


@pytest.mark.parametrize(
    ('env', 'section', 'key'),
    [
        ('REPO_ADAPTER_URL', 'repo', 'adapter_url'),
        ('GENERATE_SCHEDULE', 'generate', 'schedule'),
        ('PUBLIC_URL', None, 'public_url'),
        ('UC_ID', 'uc', 'id'),
        ('SIGNING_KEY_FILE', 'signing', 'key_file'),
        ('CERTIFICATE_MINIMUM_VALID_DAYS', None, 'certificate_minimum_valid_days'),
    ],
)
def test_toml_key_follows_the_naming_rule(env, section, key):
    assert toml_key(env) == (section, key)
    assert env_name(section, key) == env


def test_every_setting_round_trips_through_its_file_key():
    for setting in SETTINGS:
        assert env_name(*toml_key(setting.env)) == setting.env, setting.env


def test_toml_label():
    assert toml_label('REPO_URL') == '[repo] url'
    assert toml_label('MODE') == 'mode'


def test_types_groups_and_docs():
    for setting in SETTINGS:
        assert setting.type in TYPES, setting.env
        assert setting.group in GROUPS, setting.env
        assert setting.doc.endswith('.'), setting.env


def test_choice_defaults_are_choices():
    for setting in SETTINGS:
        if setting.type == 'choice':
            assert setting.choices, setting.env
            for mode in MODES:
                default = setting.default_for(mode)
                assert default == '' or default in setting.choices, setting.env


def test_int_defaults_respect_their_minimum():
    for setting in SETTINGS:
        if setting.type == 'int':
            for mode in MODES:
                assert int(setting.default_for(mode)) >= (setting.minimum or 0), setting.env


def test_mode_defaults():
    assert REGISTRY['CORE_AGE_DAYS'].default_for('online') == '400'
    assert REGISTRY['CORE_AGE_DAYS'].default_for('airgapped') == '0'
    assert REGISTRY['RETAIN_RELEASES'].default_for('airgapped') == '5'
    assert REGISTRY['POPULARITY_JSON_URL'].default_for('airgapped') == ''


def test_derived_settings_have_no_registry_default():
    for setting in SETTINGS:
        if setting.derived:
            assert (setting.default, setting.online, setting.airgapped) == ('', None, None), setting.env


def test_settings_field_names():
    assert REGISTRY['UC_IMAGE'].attribute is None
    assert REGISTRY['UC_EXTRA_ARGS'].attribute == 'extra_args'
    assert REGISTRY['UC_DATA_DIR'].attribute == 'data_dir'
    assert REGISTRY['REPO_URL'].attribute == 'repo_url'


def _samples():
    for setting in SETTINGS:
        for sample in (setting.sample, setting.online_sample, setting.airgapped_sample):
            if sample:
                yield setting, sample


def test_secret_files_are_derived():
    for setting in SETTINGS:
        if setting.secret_file:
            assert setting.derived, setting.env


def test_active_modes_are_modes():
    for setting in SETTINGS:
        assert set(setting.active) <= set(MODES), setting.env


SAMPLE_CHECKS = {
    'bool': lambda sample: sample in {'true', 'false'},
    'version': lambda sample: CORE_VERSION.match(sample) is not None,
    'url': lambda sample: urlsplit(sample).scheme in {'http', 'https'} and bool(urlsplit(sample).netloc),
    'list': lambda sample: bool(sample.split()) and ',' not in sample,
}
"""How a sample of each type must parse; only types some setting has a sample of, so no check is dead (test_every_checked_type_has_a_sample)."""
UNCHECKED_SAMPLE_TYPES = ('str', 'path', 'cron')
"""Sample types with nothing to parse here: free text, and cron, which uc schedule checks when it starts. A sample of any other type needs a check."""


@pytest.mark.parametrize('setting_and_sample', list(_samples()), ids=lambda pair: pair[0].env)
def test_every_sample_parses_for_its_type(setting_and_sample):
    setting, sample = setting_and_sample
    if setting.type in UNCHECKED_SAMPLE_TYPES:
        return
    assert setting.type in SAMPLE_CHECKS, f'{setting.env}: no check for a sample of type {setting.type}'
    assert SAMPLE_CHECKS[setting.type](sample), setting.env


def test_every_checked_type_has_a_sample():
    assert set(SAMPLE_CHECKS) <= {setting.type for setting, _ in _samples()}


def test_every_hint_line_and_doc_is_a_sentence():
    for setting in SETTINGS:
        for line in (setting.doc, *setting.hint, *setting.online_hint, *setting.airgapped_hint):
            assert line.endswith('.'), (setting.env, line)


def test_every_group_is_used():
    assert {setting.group for setting in SETTINGS} == set(GROUPS)


def test_only_url_settings_can_mean_off_when_empty():
    for setting in SETTINGS:
        if setting.empty_means_off:
            assert setting.type == 'url', setting.env


def test_a_mode_sample_comes_before_the_common_one():
    popularity = REGISTRY['POPULARITY_JSON_URL']
    assert popularity.sample_for('airgapped') == popularity.airgapped_sample != ''
    assert popularity.sample_for('online') == popularity.sample == ''
    assert REGISTRY['PUBLIC_URL'].sample_for('online') == REGISTRY['PUBLIC_URL'].sample
    assert REGISTRY['REPO_TYPE'].hint_for('online') == REGISTRY['REPO_TYPE'].online_hint
    assert REGISTRY['REPO_TYPE'].hint_for('airgapped') == ()


PROXY_VARIABLES = {'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY'}
"""The settings apply_process_env exports that the repo-adapter uses: urllib takes the proxies from the environment."""
NOT_READ_BY_THE_ADAPTER = {'JAVA_TOOL_OPTIONS': "only the generator's JVM uses it"}
"""The rest of PROCESS_ENV, each with the reason the repo-adapter does not use it."""


def test_every_process_env_variable_is_classified():
    assert not PROXY_VARIABLES & set(NOT_READ_BY_THE_ADAPTER)
    assert PROXY_VARIABLES | set(NOT_READ_BY_THE_ADAPTER) == set(config.PROCESS_ENV), 'add each PROCESS_ENV variable to PROXY_VARIABLES if the repo-adapter uses it, else to NOT_READ_BY_THE_ADAPTER with the reason'


def test_restart_marks_what_the_services_read_at_start(monkeypatch, tmp_path):
    marked = {setting.env for setting in SETTINGS if setting.restart}
    schedules = {'GENERATE_SCHEDULE', 'DOCTOR_SCHEDULE'}
    web_inputs = recorded_web_inputs(monkeypatch)
    adapter_reads = recorded_repo_adapter_reads(monkeypatch, tmp_path)
    assert {'REPO_URL', 'REPO_KEY'} | PROXY_VARIABLES <= adapter_reads  # so a recorder that records nothing cannot pass
    assert schedules <= marked
    assert web_inputs <= marked
    assert adapter_reads <= marked
    # A setting the repo-adapter never reads still needs the mark when a value it reads follows it:
    # REPO_ADAPTER_DIR's default follows UC_DATA_DIR, the secret files' SECRETS_DIR, REPO_ADAPTER's MODE.
    adapter_inputs = adapter_reads | {name for name in marked - adapter_reads - schedules if read_values_follow(name, adapter_reads)}
    unexplained = marked - web_inputs - adapter_inputs - schedules
    assert not unexplained, f'restart-marked, but no service reads them at start and no value the repo-adapter reads changes with them: {sorted(unexplained)}'


def test_restart_leaves_out_what_every_run_rereads():
    for name in ('RETAIN_RELEASES', 'JAVA_TOOL_OPTIONS', 'UC_EXTRA_ARGS', 'MIN_PLUGINS'):
        assert not REGISTRY[name].restart, name
    assert not any(setting.restart for setting in SETTINGS if setting.env_only)


def recorded_web_inputs(monkeypatch):
    """Return every setting load_web_settings reads, over the modes and backends that make it read more."""
    seen: set[str] = set()

    class Recording(config.Resolver):
        def raw(self, name):
            seen.add(name)
            return super().raw(name)

    base = {'PUBLIC_URL': 'https://uc.example.com', 'REPO_URL': 'https://repo.corp/artifactory/', 'REPO_KEY': 'jenkins'}
    cases = [
        {'MODE': 'online'},
        {'MODE': 'airgapped'},
        {'MODE': 'airgapped', 'DOWNLOAD_BACKEND': 'artifactory'},
        {'MODE': 'airgapped', 'DOWNLOAD_BACKEND': 'artifactory', 'REPO_TYPE': 'artifact-keeper'},
    ]
    with monkeypatch.context() as patch:  # undone on return, so later loads in the same test are not recorded
        patch.setattr(config, 'Resolver', Recording)
        for case in cases:
            config.load_web_settings({**base, **case}, config_file=Path('none.toml'))
    return seen


class RecordingSettings:
    """Settings that record the name of every field and property read through them.

    A property runs with the recorder as self, so the fields it reads are recorded too.
    """

    def __init__(self, settings: Settings, seen: set[str]) -> None:
        """Read from settings, adding each name read to seen."""
        self.settings, self.seen = settings, seen

    def __getattr__(self, name):
        """Record name, then read it from the settings, or run the property with this recorder as self."""
        self.seen.add(name)
        member = getattr(Settings, name, None)
        return member.fget(self) if isinstance(member, property) else getattr(self.settings, name)


def recorded_repo_adapter_reads(monkeypatch, tmp_path):
    """Return every setting `uc repo-adapter` reads from Settings as it starts, for an air-gapped Artifactory and Artifact Keeper repository.

    It runs the real command, so what the CLI reads before serving counts too. Left out: NOT_READ_BY_THE_ADAPTER,
    the PROCESS_ENV settings apply_process_env reads for every command and the repo-adapter does not use.
    """
    seen: set[str] = set()
    started = []

    class Started(ThreadingHTTPServer):
        def __init__(self, _address, handler):  # any free loopback port: what it reads matters here, not where it listens
            super().__init__(('127.0.0.1', 0), handler)
            started.append(self)

        def serve_forever(self, poll_interval=0.5):
            """Return at once, as if the process had stopped."""

    secrets = tmp_path / 'secrets'
    secrets.mkdir()
    (secrets / 'repo_username').write_text('uc\n', encoding='utf-8')
    (secrets / 'repo_password').write_text('secret\n', encoding='utf-8')
    base = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc.example.com', 'REPO_KEY': 'jenkins', 'UC_DATA_DIR': str(tmp_path / 'data'), 'REPO_ADAPTER_DIR': str(tmp_path / 'adapter'), 'SECRETS_DIR': str(secrets)}
    repositories = [
        {'REPO_TYPE': 'artifactory', 'REPO_URL': 'https://repo.corp/artifactory/'},
        {'REPO_TYPE': 'artifact-keeper', 'REPO_URL': 'https://keeper.corp/'},
    ]
    with monkeypatch.context() as patch:
        patch.setattr(adapter_server, 'ThreadingHTTPServer', Started)
        patch.setattr(adapter_server, 'PORT_FILE', tmp_path / 'adapter.port')

        def start(env):
            settings = RecordingSettings(config.load_settings(env, config_file=tmp_path / 'none.toml'), seen)
            patch.setattr(cli, 'load_settings', lambda: settings)
            try:
                assert cli.main(['repo-adapter']) == 0
            finally:
                while started:
                    started.pop().server_close()

        for repository in repositories:
            start({**base, **repository})
    fields = {setting.attribute: setting.env for setting in SETTINGS if setting.attribute}
    properties = {name for name, member in vars(Settings).items() if isinstance(member, property)}
    assert seen - set(fields) <= properties  # a field without a setting would go unchecked
    return {fields[name] for name in seen if name in fields} - set(NOT_READ_BY_THE_ADAPTER)


VARIATION_BASE = {'MODE': 'airgapped', 'PUBLIC_URL': 'https://uc.example.com', 'REPO_URL': 'https://repo.corp/artifactory/', 'REPO_KEY': 'jenkins'}
"""The settings read_values_follow and the other-value check change one setting from; every derived setting stays at its default."""


def read_values_follow(name, reads):
    """Return whether the value of a setting in reads changes when name does, with every derived setting left at its default."""
    before = config.load_settings(VARIATION_BASE, config_file=Path('none.toml'))
    setting = REGISTRY[name]
    after = config.load_settings({**VARIATION_BASE, name: other_value(setting, str(getattr(before, setting.attribute)))}, config_file=Path('none.toml'))
    return any(getattr(before, REGISTRY[read].attribute) != getattr(after, REGISTRY[read].attribute) for read in reads)


OTHER_VALUES = {
    'str': lambda _setting, current: 'other' if current != 'other' else 'another',
    'bool': lambda _setting, current: 'false' if current == 'True' else 'true',
    'int': lambda _setting, current: str(int(current) + 1),
    'ratio': lambda _setting, current: '0.5' if current != '0.5' else '0.25',
    'choice': lambda setting, current: next(choice for choice in setting.choices if choice != current),
    'cron': lambda _setting, current: '17 4 * * *' if current != '17 4 * * *' else '18 4 * * *',
    'path': lambda _setting, current: current.rstrip('/') + '/elsewhere',
    'url': lambda _setting, current: 'https://elsewhere.example' if current != 'https://elsewhere.example' else 'https://other.example',
    'version': lambda _setting, current: '2.462' if current != '2.462' else '2.479',
    'list': lambda _setting, current: '2.479.1' if current != "('2.479.1',)" else '2.462.3',
}
"""For each setting type, how other_value finds a valid value other than current, the str() of the setting's Settings field."""


def other_value(setting, current):
    """Return a valid value for setting other than current."""
    return OTHER_VALUES[setting.type](setting, current)


def test_other_value_has_a_valid_value_for_every_type():
    assert set(OTHER_VALUES) == set(TYPES)
    before = config.load_settings(VARIATION_BASE, config_file=Path('none.toml'))
    for setting in SETTINGS:
        if setting.attribute:
            current = getattr(before, setting.attribute)
            after = config.load_settings({**VARIATION_BASE, setting.env: other_value(setting, str(current))}, config_file=Path('none.toml'))
            assert getattr(after, setting.attribute) != current, setting.env
