"""The settings registry: names, types, groups and defaults.

See docs/reference/configuration.md#how-settings-are-read.
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from uc.settings import (
    CORE_VERSION,
    GROUPS,
    MODES,
    REGISTRY,
    SETTINGS,
    TYPES,
    Setting,
)


def test_names_are_unique_upper_case_variables():
    names = [setting.env for setting in SETTINGS]
    assert len(names) == len(set(names))
    assert all(re.fullmatch(r'[A-Z][A-Z0-9_]*', name) for name in names)


def test_every_type_is_known():
    for setting in SETTINGS:
        assert setting.type in TYPES, setting.env


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
                assert int(setting.default_for(mode)) >= (setting.minimum or 0), (
                    setting.env
                )


def test_mode_defaults():
    assert REGISTRY['CORE_AGE_DAYS'].default_for('online') == '400'
    assert REGISTRY['CORE_AGE_DAYS'].default_for('airgapped') == '0'
    assert REGISTRY['RETAIN_RELEASES'].default_for('airgapped') == '5'
    assert REGISTRY['POPULARITY_JSON_URL'].default_for('airgapped') == ''


def test_derived_settings_have_no_registry_default():
    for setting in SETTINGS:
        if setting.derived:
            assert (setting.default, setting.online, setting.airgapped) == (
                '',
                None,
                None,
            ), setting.env


def test_settings_field_names():
    assert REGISTRY['UC_IMAGE'].attribute is None
    assert REGISTRY['UC_EXTRA_ARGS'].attribute == 'extra_args'
    assert REGISTRY['REPO_URL'].attribute == 'repo_url'


def _samples():
    for setting in SETTINGS:
        for sample in (setting.sample, setting.online_sample, setting.airgapped_sample):
            if sample:
                yield setting, sample


def test_active_modes_are_modes():
    for setting in SETTINGS:
        assert set(setting.active) <= set(MODES), setting.env


SAMPLE_CHECKS = {
    'bool': lambda sample: sample in {'true', 'false'},
    'version': lambda sample: CORE_VERSION.match(sample) is not None,
    'url': lambda sample: (
        urlsplit(sample).scheme in {'http', 'https'} and bool(urlsplit(sample).netloc)
    ),
}
"""How a sample of each type must parse.

A sample of a type with no check here fails test_every_sample_parses_for_its_type.
"""
UNCHECKED_SAMPLE_TYPES = ('str', 'path', 'cron')
"""Sample types with nothing to parse here.

Free text, and cron, which uc schedule checks when it starts. A sample of any other type
needs a check.
"""


@pytest.mark.parametrize(
    'setting_and_sample', list(_samples()), ids=lambda pair: pair[0].env
)
def test_every_sample_parses_for_its_type(setting_and_sample):
    setting, sample = setting_and_sample
    if setting.type in UNCHECKED_SAMPLE_TYPES:
        return
    assert setting.type in SAMPLE_CHECKS, (
        f'{setting.env}: no check for a sample of type {setting.type}'
    )
    assert SAMPLE_CHECKS[setting.type](sample), setting.env


def test_every_hint_line_and_doc_is_a_sentence():
    for setting in SETTINGS:
        for line in (
            setting.doc,
            *setting.hint,
            *setting.online_hint,
            *setting.airgapped_hint,
        ):
            assert line.endswith('.'), (setting.env, line)


def test_every_group_is_used():
    assert {setting.group for setting in SETTINGS} == set(GROUPS)


def test_only_url_settings_can_mean_off_when_empty():
    for setting in SETTINGS:
        if setting.empty_means_off:
            assert setting.type == 'url', setting.env


def test_groups_follow_the_configuration_page():
    # The page's other sections (how settings are read, mode defaults, several
    # configurations) are not groups; the groups' sections come in GROUPS' order.
    page = Path('/docs/reference/configuration.md').read_text()
    headings = re.findall(r'^## (.+)$', page, re.MULTILINE)
    assert [h for h in headings if h in GROUPS] == list(GROUPS)


def test_a_mode_sample_comes_before_the_common_one():
    both = Setting(
        'X',
        'str',
        'Testing and evaluation',
        'A setting.',
        sample='common',
        online_sample='online',
        airgapped_sample='airgapped',
    )
    assert both.sample_for('online') == 'online'
    assert both.sample_for('airgapped') == 'airgapped'
    only_common = Setting('X', 'str', 'Testing and evaluation', 'A.', sample='common')
    assert only_common.sample_for('online') == 'common'
    assert only_common.sample_for('airgapped') == 'common'
