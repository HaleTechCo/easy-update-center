"""The example Jenkins pipeline runs the procurement stages in the documented order, with the approval before the push and none when nothing was staged (docs/how-to/run-procurement-from-a-pipeline.md).

A Jenkinsfile cannot run outside Jenkins, so the stages and the approval are checked in the file; the script that decides whether anything was staged runs for real.
"""

import re
import subprocess
from pathlib import Path

import pytest

from uc.procure.staging import DEFAULT_STAGING, review_line

JENKINSFILE = Path('/examples/pipelines/Jenkinsfile')
STAGES = ['check', 'stage', 'review', 'push', 'generate', 'verify', 'clean']
AFTER_STAGING = STAGES[2:]
"""The stages a run with nothing staged skips: the review and everything after it."""
COMMANDS = ['uc config --check', 'uc doctor', 'uc procure stage', 'uc procure push', 'uc generate', 'uc status', 'uc doctor', 'uc procure clean']
"""The uc commands the stages run, in the order of the how-to's stage table."""
COMMAND = re.compile(r'docker compose run --rm (?:--no-deps )?(uc (?:config --check|procure (?:stage|push|clean)|doctor|generate|status))\b')
STAGED_FLAG = re.compile(r'env\.UC_STAGED = sh\(script: "([^"$\\]*)", returnStdout: true\)\.trim\(\)')
"""How the stage stage records whether anything was staged; a script without $ or \\ is the same in Groovy and in the shell."""
TOLERATED_DOCTOR = re.compile(r"sh\(script: 'docker compose run --rm uc doctor', returnStatus: true\)(?s:.*?)\brc > 1\b")
STAGED = f'Staging into /procurement from http://source.example/:\nstaged    io/jenkins/x.hpi\n1 staged, 0 kept, 0 problem(s)\n{review_line(DEFAULT_STAGING, 1)}\n'
NOTHING_STAGED = 'Inspecting the repository...\nnext: uc procure stage --poms\n\nStaging into /procurement from http://source.example/:\n0 staged, 0 kept, 0 problem(s)\n'
"""A stage report with nothing staged: its next: line is the kind uc doctor prints, which must not count."""


def code() -> str:
    """Return the Jenkinsfile without its comment lines, so only what runs is checked."""
    return '\n'.join(line for line in JENKINSFILE.read_text(encoding='utf-8').splitlines() if not line.lstrip().startswith('//'))


def stages() -> dict[str, str]:
    """Return each stage('name') { ... } block by name, in order, found by matching braces."""
    text, found = code(), {}
    for start in re.finditer(r"stage\('(\w+)'\) \{", text):
        depth, end = 1, start.end()
        while depth:
            depth += {'{': 1, '}': -1}.get(text[end], 0)
            end += 1
        found[start.group(1)] = text[start.end() : end]
    return found


def test_the_jenkinsfile_is_the_only_example():
    assert sorted(path.name for path in JENKINSFILE.parent.iterdir()) == ['Jenkinsfile', 'README.md']


def test_the_stages_run_their_commands_in_order():
    assert list(stages()) == STAGES
    assert COMMAND.findall(code()) == COMMANDS


def test_the_approval_comes_before_the_push_and_one_run_at_a_time():
    found = stages()
    assert re.search(r'\binput message: .*\bsubmitter: ', found['review']) is not None
    assert 'uc procure push' not in ''.join(found[name] for name in STAGES[: STAGES.index('push')])
    assert 'disableConcurrentBuilds()' in code()


def test_the_push_checks_the_reviewed_list_first():
    found = stages()
    assert 'sha256sum procurement/procurement.json >' in found['stage']
    push = found['push']
    assert push.index('unstash') < push.index('sha256sum -c') < push.index('uc procure push')
    assert 'set -eo pipefail' in push[: push.index('sha256sum -c')]


@pytest.mark.parametrize('stage', ['check', 'verify'])
def test_doctor_exit_one_does_not_fail_the_stage(stage):
    assert TOLERATED_DOCTOR.search(stages()[stage]) is not None


def test_everything_after_staging_runs_only_when_something_was_staged():
    for name, block in stages().items():
        assert ("expression { env.UC_STAGED == 'true' }" in block) is (name in AFTER_STAGING), name


@pytest.mark.parametrize(('report', 'staged'), [(STAGED, 'true'), (NOTHING_STAGED, 'false'), (None, 'false')], ids=['staged', 'nothing-staged', 'no-report'])
def test_the_staged_flag_follows_the_review_line(tmp_path, report, staged):
    found = STAGED_FLAG.search(stages()['stage'])
    assert found is not None
    if report is not None:
        (tmp_path / 'stage-report.txt').write_text(report, encoding='utf-8')
    result = subprocess.run(['sh', '-c', found.group(1)], cwd=tmp_path, env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, check=False, timeout=30)
    assert (result.returncode, result.stdout) == (0, f'{staged}\n')
