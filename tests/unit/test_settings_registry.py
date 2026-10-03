"""The settings registry: names, the uc.toml naming rule, types, groups and defaults (docs/reference/configuration.md#how-settings-are-read)."""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from uc import config
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


@pytest.mark.parametrize('setting_and_sample', list(_samples()), ids=lambda pair: pair[0].env)
def test_every_sample_parses_for_its_type(setting_and_sample):
    setting, sample = setting_and_sample
    if setting.type == 'int':
        assert int(sample) >= (setting.minimum or 0)
    elif setting.type == 'bool':
        assert sample in {'true', 'false'}
    elif setting.type == 'ratio':
        assert 0 <= float(sample) <= 1
    elif setting.type == 'version':
        assert CORE_VERSION.match(sample)
    elif setting.type == 'url':
        parts = urlsplit(sample)
        assert parts.scheme in {'http', 'https'}, setting.env
        assert parts.netloc, setting.env
    elif setting.type == 'choice':
        assert sample in setting.choices


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


REPO_ADAPTER_READS = {
    'REPO_ADAPTER', 'REPO_ADAPTER_PORT', 'REPO_ADAPTER_DIR', 'REPO_TYPE', 'REPO_URL', 'REPO_API_URL', 'REPO_KEY', 'REPO_CA_FILE',
    'REPO_MAX_ARCHIVE_MB', 'REPO_USERNAME_FILE', 'REPO_PASSWORD_FILE', 'UC_DATA_DIR', 'SECRETS_DIR', 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY',
}  # fmt: skip
"""What `uc repo-adapter` reads at start (the Settings fields serve, make_server and create_adapter use, the secret files, and the proxy variables apply_process_env exports), by variable name."""


def test_restart_marks_what_the_services_read_at_start(monkeypatch):
    marked = {setting.env for setting in SETTINGS if setting.restart}
    web_inputs = recorded_web_inputs(monkeypatch)
    assert {'GENERATE_SCHEDULE', 'DOCTOR_SCHEDULE'} <= marked
    assert web_inputs <= marked
    assert marked >= REPO_ADAPTER_READS
    assert marked == web_inputs | REPO_ADAPTER_READS | {'GENERATE_SCHEDULE', 'DOCTOR_SCHEDULE'}


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

    monkeypatch.setattr(config, 'Resolver', Recording)
    base = {'PUBLIC_URL': 'https://uc.example.com', 'REPO_URL': 'https://repo.corp/artifactory/', 'REPO_KEY': 'jenkins'}
    cases = [
        {'MODE': 'online'},
        {'MODE': 'airgapped'},
        {'MODE': 'airgapped', 'DOWNLOAD_BACKEND': 'artifactory'},
        {'MODE': 'airgapped', 'DOWNLOAD_BACKEND': 'artifactory', 'REPO_TYPE': 'artifact-keeper'},
    ]
    for case in cases:
        config.load_web_settings({**base, **case}, config_file=Path('none.toml'))
    return seen
