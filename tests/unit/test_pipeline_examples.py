"""The example pipelines run the procurement stages in the documented order, with the approval before the push (the how-to "Run procurement from a pipeline")."""

import re
from pathlib import Path

import pytest

PIPELINES = Path('/examples/pipelines')
STAGES = [
    ('check', 'uc config --check'),
    ('check', 'uc doctor'),
    ('stage', 'uc procure stage'),
    ('push', 'uc procure push'),
    ('generate', 'uc generate'),
    ('verify', 'uc status'),
    ('verify', 'uc doctor'),
    ('clean', 'uc procure clean'),
]
"""The stage table of the how-to, in order: each stage's uc commands."""
COMMAND = re.compile(r'docker compose run --rm (?:--no-deps )?(uc (?:config --check|procure (?:stage|push|clean)|doctor|generate|status))\b')
APPROVAL = {
    'Jenkinsfile': re.compile(r'\binput\s*\(?\s*message:'),
    'gitlab-ci.yml': re.compile(r'^\s*when: manual$', re.MULTILINE),
    'github-actions.yml': re.compile(r'^\s*environment: procurement\b', re.MULTILINE),
}
ONE_AT_A_TIME = {'Jenkinsfile': 'disableConcurrentBuilds()', 'gitlab-ci.yml': 'resource_group:', 'github-actions.yml': 'concurrency:'}
NAMES = sorted(APPROVAL)


def code(name: str) -> str:
    """Return a pipeline file without its comment lines, so only what runs is checked."""
    lines = (PIPELINES / name).read_text(encoding='utf-8').splitlines()
    return '\n'.join(line for line in lines if not line.lstrip().startswith(('#', '//')))


def test_every_example_is_checked():
    assert sorted(path.name for path in PIPELINES.iterdir() if path.name != 'README.md') == NAMES


@pytest.mark.parametrize('name', NAMES)
def test_the_stages_run_in_order(name):
    assert COMMAND.findall(code(name)) == [command for _, command in STAGES]


@pytest.mark.parametrize('name', NAMES)
def test_the_approval_comes_before_the_push(name):
    text = code(name)
    approval = APPROVAL[name].search(text)
    assert approval is not None, f'{name} has no approval step'
    assert approval.start() < text.index('uc procure push')


@pytest.mark.parametrize('name', NAMES)
def test_one_run_at_a_time(name):
    assert ONE_AT_A_TIME[name] in code(name)


@pytest.mark.parametrize('name', NAMES)
def test_checking_the_settings_starts_no_other_service(name):
    assert 'docker compose run --rm --no-deps uc config --check' in code(name)
