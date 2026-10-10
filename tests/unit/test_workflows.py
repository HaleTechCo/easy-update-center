"""The GitHub Actions workflows (.github/workflows): pinned actions, least permissions, the CI gate, the scheduled jobs and the release."""

import re
from pathlib import Path

import pytest
import yaml

from shellrun import run_bash, stub  # noqa: F401

GITHUB = Path(__file__).resolve().parents[2] / 'github'
"""The repository's .github directory, which make test-unit mounts at /github."""
WORKFLOWS = GITHUB / 'workflows'
PINNED = re.compile(r'^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$')
USES = re.compile(r'^\s*(?:-\s+)?uses:\s*(\S+)(.*)$', re.MULTILINE)
EXTRA_PERMISSIONS: dict[tuple[str, str], dict[str, str]] = {}
"""Every job that declares permissions, with exactly the ones it needs; any other job inherits contents: read."""
WRITES_CONTENTS: set[tuple[str, str]] = set()
"""The only jobs that may hold contents: write: the rulesets let the GitHub Actions app bypass them, so any such job could push to main."""
CHECKOUT_WITH_CREDENTIALS: set[tuple[str, str]] = set()
"""The only jobs whose checkout keeps the token, for a git push."""
TEST_TARGETS = ['test-unit', 'test-web', 'test-routing', 'test-secrets', 'test-setup', 'compose-check']
"""make test's suites and make compose-check, one CI job each."""
PATH = '/usr/bin:/bin'


def workflow_names() -> list[str]:
    """Return the file names of every workflow."""
    return sorted(path.name for path in WORKFLOWS.glob('*.yml'))


def load(name: str) -> dict:
    """Return a workflow as data, with its on: under 'on' (YAML 1.1 reads a bare on as true)."""
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding='utf-8'))
    if True in data:
        data['on'] = data.pop(True)
    return data


def jobs(name: str) -> dict[str, dict]:
    """Return a workflow's jobs by id, in file order."""
    return load(name)['jobs']


def step(job: dict, name: str) -> dict:
    """Return the step of job named name."""
    return next(each for each in job['steps'] if each.get('name') == name)


@pytest.mark.parametrize('name', workflow_names())
def test_every_action_is_pinned_to_a_commit_with_its_version(name):
    for target, rest in USES.findall((WORKFLOWS / name).read_text(encoding='utf-8')):
        if target.startswith('./'):
            continue  # a reusable workflow of this repository
        assert PINNED.match(target), f'{name}: {target} is not pinned to a commit'
        assert re.match(r'\s+# v\d', rest), f'{name}: {target} has no "# vX.Y.Z" comment'


@pytest.mark.parametrize('name', workflow_names())
def test_every_workflow_reads_contents_only_by_default_and_runs_bash_with_pipefail(name):
    data = load(name)
    assert data['permissions'] == {'contents': 'read'}
    assert data['defaults'] == {'run': {'shell': 'bash'}}


@pytest.mark.parametrize('name', workflow_names())
def test_each_job_holds_exactly_the_permissions_it_needs(name):
    for job_name, job in jobs(name).items():
        assert job.get('permissions') == EXTRA_PERMISSIONS.get((name, job_name)), f'{name}: {job_name}'


def test_only_the_jobs_that_must_write_contents_can():
    writers = {(name, job_name) for name in workflow_names() for job_name, job in jobs(name).items() if (job.get('permissions') or {}).get('contents') == 'write'}
    assert writers == WRITES_CONTENTS


@pytest.mark.parametrize('name', workflow_names())
def test_no_run_script_expands_an_expression(name):
    # An expression inside a script is pasted into it before bash parses it: values go through env:.
    for job_name, job in jobs(name).items():
        for each in job.get('steps', []):
            assert '${{' not in each.get('run', ''), f'{name}: {job_name}: {each.get("name")}'


@pytest.mark.parametrize('name', workflow_names())
def test_checkouts_drop_the_token_unless_the_job_pushes(name):
    for job_name, job in jobs(name).items():
        for each in job.get('steps', []):
            if each.get('uses', '').startswith('actions/checkout@'):
                keeps = (name, job_name) in CHECKOUT_WITH_CREDENTIALS
                assert each.get('with', {}).get('persist-credentials', True) is keeps, f'{name}: {job_name}'


def test_ci_runs_on_pull_requests_pushes_to_main_by_hand_and_for_the_release():
    on = load('ci.yml')['on']
    assert set(on) == {'pull_request', 'push', 'workflow_dispatch', 'workflow_call'}
    assert on['push'] == {'branches': ['main']}
    for trigger in ('workflow_dispatch', 'workflow_call'):
        assert on[trigger]['inputs']['ref']['type'] == 'string'


def test_ci_cancels_superseded_runs_of_pull_requests_only():
    assert load('ci.yml')['concurrency']['cancel-in-progress'] == "${{ github.event_name == 'pull_request' }}"


def test_every_ci_checkout_takes_the_ref_it_is_asked_for():
    for job_name, job in jobs('ci.yml').items():
        for each in job.get('steps', []):
            if each.get('uses', '').startswith('actions/checkout@'):
                assert each['with']['ref'] == '${{ inputs.ref }}', job_name


def test_ci_lints_with_the_pre_commit_hooks():
    assert step(jobs('ci.yml')['lint'], 'pre-commit')['run'] == 'pre-commit run --all-files --show-diff-on-failure'


def test_ci_builds_the_images_once_and_every_test_job_loads_them():
    ci = jobs('ci.yml')
    assert ci['test']['strategy']['matrix']['target'] == TEST_TARGETS
    assert ci['it-airgapped']['strategy']['matrix']['type'] == ['artifactory', 'artifact-keeper']
    for job in ('test', 'it-airgapped'):
        assert ci[job]['needs'] == 'images'
        scripts = '\n'.join(each.get('run', '') for each in ci[job]['steps'])
        assert '| docker load' in scripts
        assert 'PREBUILT=1' in scripts


def test_the_ci_check_needs_every_job_and_fails_unless_all_succeeded(tmp_path):
    ci = jobs('ci.yml')
    gate = ci['ci']
    assert set(gate['needs']) == set(ci) - {'ci'}
    assert gate['if'] == 'always()'
    script = step(gate, 'Every job succeeded')['run']
    assert run_bash(tmp_path, script, {'RESULTS': 'success success success success', 'PATH': PATH}, tmp_path).returncode == 0
    for results in ('success failure success success', 'success success skipped success', 'cancelled success success success'):
        result = run_bash(tmp_path, script, {'RESULTS': results, 'PATH': PATH}, tmp_path)
        assert result.returncode == 1, results
        assert '::error::' in result.stdout


def test_ci_needs_no_secrets():
    # Pull requests from forks get no secrets, so everything CI runs must pass without them.
    assert 'secrets.' not in (WORKFLOWS / 'ci.yml').read_text(encoding='utf-8')
