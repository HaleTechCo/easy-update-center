"""The example pipelines run the procurement stages in the documented order, with the approval before the push (docs/how-to/run-procurement-from-a-pipeline.md)."""

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
STAGE_NAMES = ['check', 'stage', 'review', 'push', 'generate', 'verify', 'clean']
"""Every stage, in order. The review runs no command; on GitHub it is the push job's environment, so that file has no review job."""
COMMAND = re.compile(r'docker compose run --rm (?:--no-deps )?(uc (?:config --check|procure (?:stage|push|clean)|doctor|generate|status))\b')
APPROVAL = {
    'Jenkinsfile': re.compile(r'\binput\s*\(?\s*message:'),
    'gitlab-ci.yml': re.compile(r'^\s*when: manual$', re.MULTILINE),
    'github-actions.yml': re.compile(r'^\s*environment: procurement\b', re.MULTILINE),
}
ONE_AT_A_TIME = {'Jenkinsfile': 'disableConcurrentBuilds()', 'gitlab-ci.yml': 'resource_group:', 'github-actions.yml': 'concurrency:'}
NAMES = sorted(APPROVAL)
GITLAB_KEYWORDS = {'variables', 'stages', 'workflow', 'default'}
DOCTOR_EXIT_ONE = re.compile(r'docker compose run --rm uc doctor \|\| \[ \$\? -eq 1 \]')
TOLERATED_DOCTOR = {
    'Jenkinsfile': re.compile(r"sh\(script: 'docker compose run --rm uc doctor', returnStatus: true\)(?s:.*?)\brc > 1\b"),
    'gitlab-ci.yml': DOCTOR_EXIT_ONE,
    'github-actions.yml': DOCTOR_EXIT_ONE,
}
KEEPS_ARTIFACTS = {
    'Jenkinsfile': re.compile(r'post\s*\{\s*always\s*\{\s*archiveArtifacts\b'),
    'gitlab-ci.yml': re.compile(r'^\s*when: always$', re.MULTILINE),
    'github-actions.yml': re.compile(r'^\s*if: always\(\)$', re.MULTILINE),
}


def code(name: str) -> str:
    """Return a pipeline file without its comment lines, so only what runs is checked."""
    lines = (PIPELINES / name).read_text(encoding='utf-8').splitlines()
    return '\n'.join(line for line in lines if not line.lstrip().startswith(('#', '//')))


def jenkins_stages(text: str) -> dict[str, str]:
    """Return each `stage('name') { ... }` block of a Jenkinsfile, found by matching braces."""
    found = {}
    for start in re.finditer(r"stage\('(\w+)'\) \{", text):
        depth = 1
        end = start.end()
        while depth:
            depth += {'{': 1, '}': -1}.get(text[end], 0)
            end += 1
        found[start.group(1)] = text[start.end() : end]
    return found


def split_blocks(text: str, header: re.Pattern[str]) -> dict[str, str]:
    """Split text at each line the header matches; a block runs to the next such line."""
    marks = [(match.group(1), match.start(), match.end()) for match in header.finditer(text)]
    ends = [mark[1] for mark in marks[1:]] + [len(text)]
    return {key: text[end:stop] for (key, _, end), stop in zip(marks, ends, strict=True)}


def blocks(name: str) -> dict[str, str]:
    """Return a pipeline's stages (Jenkins) or jobs (GitLab, GitHub) by name, in file order."""
    text = code(name)
    if name == 'Jenkinsfile':
        return jenkins_stages(text)
    if name == 'gitlab-ci.yml':
        top_level = split_blocks(text, re.compile(r'^([A-Za-z_][\w-]*):.*$', re.MULTILINE))
        return {job: block for job, block in top_level.items() if job not in GITLAB_KEYWORDS}
    return split_blocks(text.split('\njobs:\n', 1)[1], re.compile(r'^  (\w+):$', re.MULTILINE))


def expected_stages(name: str) -> list[str]:
    """Return the stages or jobs a pipeline file holds."""
    return [stage for stage in STAGE_NAMES if not (stage == 'review' and name == 'github-actions.yml')]


def test_every_example_is_checked():
    assert sorted(path.name for path in PIPELINES.iterdir() if path.name != 'README.md') == NAMES


@pytest.mark.parametrize('name', NAMES)
def test_the_stages_run_in_order(name):
    assert COMMAND.findall(code(name)) == [command for _, command in STAGES]


@pytest.mark.parametrize('name', NAMES)
def test_each_command_sits_in_its_stage(name):
    found = blocks(name)
    assert list(found) == expected_stages(name)
    for stage, block in found.items():
        assert COMMAND.findall(block) == [command for owner, command in STAGES if owner == stage], stage


@pytest.mark.parametrize('name', NAMES)
def test_the_approval_comes_before_the_push(name):
    found = blocks(name)
    text = code(name)
    approval = APPROVAL[name].search(text)
    assert approval is not None, f'{name} has no approval step'
    assert approval.start() < text.index('uc procure push')
    if name == 'Jenkinsfile':
        assert list(found).index('review') < list(found).index('push')
        assert APPROVAL[name].search(found['review']) is not None
        assert 'submitter:' in found['review']
    elif name == 'gitlab-ci.yml':
        listed = re.search(r'^stages: \[(.*)\]$', text, re.MULTILINE)
        assert listed is not None
        order = listed.group(1).split(', ')
        assert order.index('review') < order.index('push')
        assert 'when: manual' in found['review']
        assert 'allow_failure: false' in found['review']
        assert 'environment: procurement' in found['review']
        assert 'needs:' not in found['push'] or 'review' in found['push'].split('needs:')[1].splitlines()[0]
        for job, block in found.items():
            assert f'stage: {job}\n' in block, job
    else:
        assert APPROVAL[name].search(found['push']) is not None
        assert 'needs: stage' in found['push']


@pytest.mark.parametrize('name', NAMES)
def test_the_push_checks_what_was_reviewed(name):
    found = blocks(name)
    assert 'sha256sum procurement/procurement.json >' in found['stage']
    push = found['push']
    assert 'sha256sum -c' in push
    assert push.index('sha256sum -c') < push.index('uc procure push')
    if name == 'github-actions.yml':
        assert push.index('actions/download-artifact@v4') < push.index('sha256sum -c')
    if name == 'Jenkinsfile':
        assert push.index('unstash') < push.index('sha256sum -c')
        assert re.search(r'set -e\w*', push[: push.index('sha256sum -c')]) is not None
    if name == 'gitlab-ci.yml':
        assert all('||' not in line for line in push.splitlines() if 'sha256sum -c' in line)
    if name == 'github-actions.yml':
        assert 'continue-on-error' not in push


@pytest.mark.parametrize('name', NAMES)
def test_the_reviewers_keep_the_artifacts_when_the_stage_fails(name):
    assert KEEPS_ARTIFACTS[name].search(blocks(name)['stage']) is not None


@pytest.mark.parametrize('name', NAMES)
def test_doctor_exit_one_does_not_fail_check_or_verify(name):
    found = blocks(name)
    for stage in ('check', 'verify'):
        assert len(TOLERATED_DOCTOR[name].findall(found[stage])) == 1, stage


@pytest.mark.parametrize('name', NAMES)
def test_one_run_at_a_time(name):
    assert ONE_AT_A_TIME[name] in code(name)
    if name == 'gitlab-ci.yml':
        for job, block in blocks(name).items():
            assert 'resource_group: jenkins-update-center\n' in block, job
        assert 'resource_group' not in code(name).split('\ndefault:\n')[1].split('\n\n')[0]


@pytest.mark.parametrize('name', NAMES)
def test_checking_the_settings_starts_no_other_service(name):
    assert 'docker compose run --rm --no-deps uc config --check' in blocks(name)['check']
