"""The example settings files: generated from the registry, complete, working, and committed up to date."""

import re
import tomllib
from pathlib import Path

import pytest

from uc.config import load_settings, read_config_file
from uc.examples import example_env, example_toml
from uc.settings import MODES, SETTINGS, toml_key

# The committed copies, mounted by `make test-unit` (Makefile SRC_MOUNTS).
COMMITTED = Path('/examples')


@pytest.mark.parametrize(
    ('name', 'make'),
    [
        ('uc.example.toml', lambda: example_toml('online')),
        ('env.online.example', lambda: example_env('online')),
        ('env.airgapped.example', lambda: example_env('airgapped')),
    ],
)
def test_the_committed_examples_are_current(name, make):
    assert (COMMITTED / name).read_text(encoding='utf-8') == make(), f'{name} is out of date: run make examples'


@pytest.mark.parametrize('mode', MODES)
def test_the_env_example_lists_every_setting(mode):
    text = example_env(mode)
    for setting in SETTINGS:
        assert re.search(rf'^(# )?{setting.env}=', text, re.MULTILINE), setting.env


def test_the_toml_example_lists_every_setting_the_file_can_hold():
    uncommented = '\n'.join(line[2:] if re.match(r'# [a-z0-9_]+ = ', line) else line for line in example_toml('online').splitlines())
    document = tomllib.loads(uncommented)
    for setting in SETTINGS:
        if setting.env_only:
            continue
        section, key = toml_key(setting.env)
        assert key in (document[section] if section else document), setting.env


@pytest.mark.parametrize('mode', MODES)
def test_the_active_lines_of_each_example_are_a_working_configuration(mode, tmp_path):
    active = dict(line.split('=', 1) for line in example_env(mode).splitlines() if line and not line.startswith('#'))
    assert load_settings(active, config_file=tmp_path / 'none.toml').mode == mode
    path = tmp_path / 'uc.toml'
    path.write_text(example_toml(mode), encoding='utf-8')
    assert load_settings({}, config_file=path).mode == mode


def test_the_online_toml_example_sets_only_the_required_settings(tmp_path):
    path = tmp_path / 'uc.toml'
    path.write_text(example_toml('online'), encoding='utf-8')
    assert read_config_file(path) == {'MODE': 'online', 'PUBLIC_URL': 'https://jenkins-updates.example.com'}
