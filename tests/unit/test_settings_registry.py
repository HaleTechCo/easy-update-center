"""The settings registry: names, the uc.toml naming rule, types, groups and defaults (docs/reference/configuration.md#how-settings-are-read)."""

import re

import pytest

from uc.settings import GROUPS, MODES, REGISTRY, SETTINGS, TYPES, env_name, toml_key, toml_label


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
