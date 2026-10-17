"""The example .env files.

Generated from the registry, complete, working, and committed up to date.
"""

import re
from pathlib import Path

import pytest

from uc.config import load_settings
from uc.examples import example_env
from uc.settings import MODES, SETTINGS

# The committed copies, mounted by `make test-unit` (Makefile SRC_MOUNTS).
COMMITTED = Path('/examples')


@pytest.mark.parametrize(
    ('name', 'mode'),
    [('env.online.example', 'online'), ('env.airgapped.example', 'airgapped')],
)
def test_the_committed_examples_are_current(name, mode):
    assert (COMMITTED / name).read_text(encoding='utf-8') == example_env(mode), (
        f'{name} is out of date: run make examples'
    )


def test_each_example_says_where_its_mode_reads_and_downloads_from():
    online, airgapped = example_env('online'), example_env('airgapped')
    assert (
        '# Online, the generator reads repo.jenkins-ci.org with a free Jenkins '
        'community account\n'
        '# (secrets/repo_*), and controllers download from get.jenkins.io'
    ) in online
    assert (
        '# Air-gapped, the generator reads your repository through the repo-adapter\n'
        '# (docs/how-to/stock-airgapped-repository.md), and controllers download '
        'from it.\n'
    ) in airgapped
    assert 'reads your repository through the repo-adapter' not in online
    assert 'community account' not in airgapped


def test_the_ca_settings_say_where_to_mount_the_bundle():
    text = example_env('airgapped')
    assert (
        '# Mount the bundle into the repo-adapter and uc and point this at it. '
        'The generator never uses it: a private CA needs REPO_ADAPTER=on. '
        'See docs/getting-started/airgapped.md#repository-behind-a-private-ca.\n'
        '# REPO_CA_FILE='
    ) in text
    assert (
        '# Mount the bundle into web and point this at it. '
        'See docs/how-to/proxy-downloads.md#backend-with-a-private-ca.\n'
        '# DOWNLOAD_PROXY_CA_FILE='
    ) in text


@pytest.mark.parametrize('mode', MODES)
def test_the_env_example_lists_every_setting(mode):
    text = example_env(mode)
    for setting in SETTINGS:
        assert re.search(rf'^(# )?{setting.env}=', text, re.MULTILINE), setting.env


@pytest.mark.parametrize('mode', MODES)
def test_the_active_lines_of_each_example_are_a_working_configuration(mode):
    active = dict(
        line.split('=', 1)
        for line in example_env(mode).splitlines()
        if line and not line.startswith('#')
    )
    assert load_settings(active).mode == mode


def test_the_online_example_says_how_to_use_your_own_repository():
    online, airgapped = example_env('online'), example_env('airgapped')
    hint = (
        '# Your own Artifactory or Artifact Keeper instead of repo.jenkins-ci.org? '
        'Set REPO_TYPE, REPO_URL and REPO_KEY here.\n'
        '# Also set DOWNLOAD_BACKEND=artifactory, and usually CORE_AGE_DAYS=0. '
        'See docs/architecture/modes.md#your-own-repository.\n'
        '# REPO_TYPE=artifactory\n'
    )
    assert hint in online
    assert 'Your own Artifactory or Artifact Keeper instead' not in airgapped


def test_the_airgapped_example_explains_core_age_days():
    assert (
        '# 400 behaves like updates.jenkins.io (tiers only for cores released in '
        'the last 400 days).\n'
        '# CORE_AGE_DAYS=0\n'
    ) in example_env('airgapped')
    assert '400 behaves like' not in example_env('online')
    assert '# CORE_AGE_DAYS=400\n' in example_env('online')


def test_the_airgapped_example_shows_procured_upstream_data():
    text = example_env('airgapped')
    assert (
        '# A copy you procured, to keep popularity. '
        'See docs/how-to/keep-upstream-data-fresh.md.\n'
        '# POPULARITY_JSON_URL=https://artifactory.corp.example/artifactory/generic-local/latestNumbers.json\n'
    ) in text
    assert (
        '# A copy you procured, to keep the tool installers. '
        'See docs/how-to/keep-upstream-data-fresh.md.\n'
        '# TOOL_INSTALLERS_URL=https://artifactory.corp.example/artifactory/generic-local/updates/\n'
    ) in text
    online = example_env('online')
    assert '# POPULARITY_JSON_URL=https://raw.githubusercontent.com/' in online
    assert 'generic-local/latestNumbers.json' not in online
    assert 'generic-local/updates/' not in online


def test_the_compose_file_example_differs_by_mode():
    airgapped, online = example_env('airgapped'), example_env('online')
    assert (
        '# Its default is compose.yaml alone: uncomment this so plain docker '
        'compose uses the air-gapped override.\n'
        '# COMPOSE_FILE=compose.yaml:compose.airgapped.yaml\n'
    ) in airgapped
    assert '# COMPOSE_FILE=\n' in online
    assert 'air-gapped override' not in online


def test_the_header_says_what_a_commented_line_shows():
    assert (
        'a line starting with # is not set: it shows the default or an example '
        'value, and the comment above it says what applies when it is unset'
    ) in example_env('online').replace('\n# ', ' ')


@pytest.mark.parametrize('mode', MODES)
def test_the_examples_never_mention_a_settings_file(mode):
    assert 'uc.toml' not in example_env(mode)
