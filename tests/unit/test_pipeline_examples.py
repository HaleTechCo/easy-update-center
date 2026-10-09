"""The example pipelines run the procurement stages in the documented order, with the approval before the push and none when nothing was staged (docs/how-to/run-procurement-from-a-pipeline.md)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from uc.procure.staging import DEFAULT_STAGING, review_line

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
AFTER_STAGING = STAGE_NAMES[2:]
"""The stages a run with nothing staged skips: the review and everything after it."""
GITLAB_CHILD = 'gitlab-ci-procure.yml'
"""The GitLab example's second file: the child pipeline that gitlab-ci.yml's procure job starts when something was staged."""
FILES = {'Jenkinsfile': ('Jenkinsfile',), 'gitlab-ci.yml': ('gitlab-ci.yml', GITLAB_CHILD), 'github-actions.yml': ('github-actions.yml',)}
"""Each example by its main file, with every file it is made of, in the order they run."""
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
REVIEWERS_STEP = 'Check that the procurement environment requires reviewers'
"""The GitHub push job's first step: it stops the job unless the procurement environment has required reviewers."""
STAGE_STEP = 'Stage the missing files for review'
"""The GitHub stage job's step that runs uc procure stage and tells the push job whether anything was staged."""
BYPASS_WARNING = '::warning title=Approval can be bypassed::'
CURL_STUB = """#!/bin/bash
printf '%s\\n' "$@" > "$STUB_DIR/curl-args"
[ -e "$STUB_DIR/answer.json" ] || { echo 'curl: (22) The requested URL returned error: 404' >&2; exit 22; }
cat "$STUB_DIR/answer.json"
"""
"""Stands in for curl in the reviewers check: records its arguments and prints answer.json, or fails as curl -f does on an HTTP error."""
DOCKER_STUB = """#!/bin/bash
printf '%s\\n' "$@" > "$STUB_DIR/docker-args"
cat "$STUB_DIR/report"
exit "$(cat "$STUB_DIR/rc")"
"""
"""Stands in for docker in the stage step: records its arguments, prints the stage report it is given and exits with the code it is given."""
JENKINS_STAGED = re.compile(r'env\.UC_STAGED = sh\(script: "([^"$\\]*)", returnStdout: true\)\.trim\(\)')
"""How the Jenkinsfile's stage stage records whether anything was staged; a script without $ or \\ is the same in Groovy and in the shell."""
GITLAB_DOTENV = re.compile(r'^  artifacts:\n(?:    .*\n)*?    reports:\n      dotenv: ([\w.-]+\.env)$', re.MULTILINE)
"""The dotenv report of a GitLab job: the file whose variables later jobs, and through the procure job the child pipeline, receive."""
JENKINS_WHEN = re.compile(r"\bwhen \{\n\s*(?:(beforeAgent true)\n\s*)?expression \{ env\.UC_STAGED == 'true' \}\n\s*\}")
NOTHING_STAGED = 'Inspecting the repository...\nnext: uc procure stage --poms (or set PROCUREMENT_POMS)\n\nStaging into /procurement from http://source.example/:\n0 staged, 0 kept, 0 problem(s)\n'
"""A stage report with nothing staged, so without the review line; its other next: line, the kind uc doctor prints, must not count."""
STAGED = f'Inspecting the repository...\n\nStaging into /procurement from http://source.example/:\nstaged    io/jenkins/x.hpi\n1 staged, 0 kept, 0 problem(s)\n{review_line(DEFAULT_STAGING, 1)}\n'
"""A stage report with one file staged, ending with the review line uc procure stage prints only when procurement.json lists files."""
REPORTS = [pytest.param(report, rc, staged, id=f'{label}-exit-{rc}') for report, staged, label in ((STAGED, True, 'staged'), (NOTHING_STAGED, False, 'nothing-staged')) for rc in (0, 1)]
"""Stage reports with the exit code of uc procure stage, and whether anything was staged."""


def code(name: str) -> str:
    """Return a pipeline file without its comment lines, so only what runs is checked."""
    lines = (PIPELINES / name).read_text(encoding='utf-8').splitlines()
    return '\n'.join(line for line in lines if not line.lstrip().startswith(('#', '//')))


def example(name: str) -> str:
    """Return the code of every file an example is made of, in the order they run."""
    return '\n'.join(code(file) for file in FILES[name])


def block_end(text: str, start: int) -> int:
    """Return where the brace block whose body starts at start ends, just after its closing brace."""
    depth = 1
    end = start
    while depth:
        depth += {'{': 1, '}': -1}.get(text[end], 0)
        end += 1
    return end


def jenkins_stages(text: str) -> dict[str, str]:
    """Return each `stage('name') { ... }` block of a Jenkinsfile, found by matching braces."""
    return {start.group(1): text[start.end() : block_end(text, start.end())] for start in re.finditer(r"stage\('(\w+)'\) \{", text)}


def split_blocks(text: str, header: re.Pattern[str]) -> dict[str, str]:
    """Split text at each line the header matches; a block runs to the next such line."""
    marks = [(match.group(1), match.start(), match.end()) for match in header.finditer(text)]
    ends = [mark[1] for mark in marks[1:]] + [len(text)]
    return {key: text[end:stop] for (key, _, end), stop in zip(marks, ends, strict=True)}


def gitlab_jobs(file: str) -> dict[str, str]:
    """Return the jobs of one GitLab CI file by name, in file order."""
    top_level = split_blocks(code(file), re.compile(r'^([A-Za-z_][\w-]*):.*$', re.MULTILINE))
    return {job: block for job, block in top_level.items() if job not in GITLAB_KEYWORDS}


def gitlab_stages(file: str) -> list[str]:
    """Return the stages a GitLab CI file lists, in order."""
    listed = re.search(r'^stages: \[(.*)\]$', code(file), re.MULTILINE)
    assert listed is not None, file
    return listed.group(1).split(', ')


def gitlab_default(file: str) -> str:
    """Return the default: section of a GitLab CI file."""
    return code(file).split('\ndefault:\n')[1].split('\n\n')[0]


def blocks(name: str) -> dict[str, str]:
    """Return a pipeline's stages (Jenkins) or jobs (GitLab, both files; GitHub) by name, in the order they run."""
    if name == 'Jenkinsfile':
        return jenkins_stages(code(name))
    if name == 'gitlab-ci.yml':
        return {job: block for file in FILES[name] for job, block in gitlab_jobs(file).items()}
    return split_blocks(code(name).split('\njobs:\n', 1)[1], re.compile(r'^  (\w+):$', re.MULTILINE))


def expected_stages(name: str) -> list[str]:
    """Return the stages or jobs a pipeline holds: GitHub has no review job, and GitLab's procure job starts the child pipeline."""
    if name == 'github-actions.yml':
        return [stage for stage in STAGE_NAMES if stage != 'review']
    if name == 'gitlab-ci.yml':
        return [*STAGE_NAMES[:2], 'procure', *AFTER_STAGING]
    return STAGE_NAMES


def github_step(job: str, name: str) -> str:
    """Return the step of a GitHub job with this name, up to the next step."""
    start = job.index(f'      - name: {name}\n')
    end = job.find('\n      - ', start)
    return job[start:] if end == -1 else job[start:end]


def github_script(job: str, name: str) -> str:
    """Return the run: | script of a GitHub step, as the runner runs it."""
    step = github_step(blocks('github-actions.yml')[job], name)
    return '\n'.join(line[10:] for line in step.split('\n        run: |\n', 1)[1].splitlines()) + '\n'


def gitlab_script(block: str, key: str) -> list[str]:
    """Return the commands of a GitLab script list (one-line items and | blocks), as the runner runs them."""
    commands: list[str] = []
    for line in block.split(f'\n  {key}:\n', 1)[1].splitlines():
        if not line.startswith('    '):
            break
        if line == '    - |':
            commands.append('')
        elif line.startswith('    - '):
            commands.append(line[6:])
        else:
            commands[-1] += line[6:] + '\n'
    return commands


def stub(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable stub command into tmp_path/bin; return that directory."""
    stubs = tmp_path / 'bin'
    stubs.mkdir(exist_ok=True)
    (stubs / name).write_text(body, encoding='utf-8')
    (stubs / name).chmod(0o755)
    return stubs


def run_bash(tmp_path: Path, script: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a script as a GitHub bash step or a GitLab shell runner job runs it: bash with -e and pipefail."""
    (tmp_path / 'step.sh').write_text(script, encoding='utf-8')
    return subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', str(tmp_path / 'step.sh')], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=30)


def run_reviewers_check(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the GitHub reviewers check against a stub curl that answers with tmp_path/answer.json, or fails without one."""
    stubs = stub(tmp_path, 'curl', CURL_STUB)
    env = {'PATH': f'{stubs}:/usr/bin:/bin', 'STUB_DIR': str(tmp_path), 'GH_TOKEN': 'token-1', 'GITHUB_API_URL': 'https://api.github.example', 'GITHUB_REPOSITORY': 'corp/uc-pipelines'}
    return run_bash(tmp_path, github_script('push', REVIEWERS_STEP), env, tmp_path)


def deployment(tmp_path: Path, report: str, rc: int) -> tuple[Path, dict[str, str]]:
    """Make a deployment directory with a staged procurement.json, and a stub docker whose uc procure stage prints report and exits rc; return the directory and the environment."""
    uc_dir = tmp_path / 'uc'
    (uc_dir / 'procurement').mkdir(parents=True)
    (uc_dir / 'procurement' / 'procurement.json').write_text('{"format": 1, "files": {}}\n', encoding='utf-8')
    (tmp_path / 'report').write_text(report, encoding='utf-8')
    (tmp_path / 'rc').write_text(str(rc), encoding='utf-8')
    stubs = stub(tmp_path, 'docker', DOCKER_STUB)
    return uc_dir, {'PATH': f'{stubs}:/usr/bin:/bin', 'STUB_DIR': str(tmp_path), 'UC_DIR': str(uc_dir)}


def run_gitlab_stage_job(tmp_path: Path, report: str, rc: int) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the GitLab stage job, before_script included, in a project directory holding both example files; return the result and that directory."""
    _, env = deployment(tmp_path, report, rc)
    project = tmp_path / 'project'
    project.mkdir()
    for file in FILES['gitlab-ci.yml']:
        shutil.copy(PIPELINES / file, project / file)
    commands = gitlab_script(gitlab_default('gitlab-ci.yml'), 'before_script') + gitlab_script(gitlab_jobs('gitlab-ci.yml')['stage'], 'script')
    return run_bash(tmp_path, '\n'.join(commands) + '\n', {**env, 'CI_PROJECT_DIR': str(project)}, project), project


def test_every_example_is_checked():
    assert sorted(path.name for path in PIPELINES.iterdir() if path.name != 'README.md') == sorted(file for files in FILES.values() for file in files)


@pytest.mark.parametrize('name', NAMES)
def test_the_stages_run_in_order(name):
    assert COMMAND.findall(example(name)) == [command for _, command in STAGES]


@pytest.mark.parametrize('name', NAMES)
def test_each_command_sits_in_its_stage(name):
    found = blocks(name)
    assert list(found) == expected_stages(name)
    for stage, block in found.items():
        assert COMMAND.findall(block) == [command for owner, command in STAGES if owner == stage], stage


@pytest.mark.parametrize('name', NAMES)
def test_the_approval_comes_before_the_push(name):
    found = blocks(name)
    text = example(name)
    approval = APPROVAL[name].search(text)
    assert approval is not None, f'{name} has no approval step'
    assert approval.start() < text.index('uc procure push')
    if name == 'Jenkinsfile':
        assert list(found).index('review') < list(found).index('push')
        assert APPROVAL[name].search(found['review']) is not None
        assert 'submitter:' in found['review']
    elif name == 'gitlab-ci.yml':
        assert gitlab_stages('gitlab-ci.yml') == ['check', 'stage', 'procure']
        assert gitlab_stages(GITLAB_CHILD) == AFTER_STAGING
        assert APPROVAL[name].search(code('gitlab-ci.yml')) is None  # the approval is in the child pipeline only
        assert 'when: manual' in found['review']
        assert 'allow_failure: false' in found['review']
        assert 'environment: procurement' in found['review']
        # needs makes push wait for the jobs it lists only: without review there, it would not wait for the approval.
        assert '\n  needs:\n    - pipeline: $PARENT_PIPELINE_ID\n      job: stage\n    - review\n' in found['push']
        for job, block in found.items():
            assert f'stage: {job}\n' in block, job
    else:
        assert APPROVAL[name].search(found['push']) is not None
        assert 'needs: stage' in found['push']


def test_the_github_push_job_first_checks_that_the_environment_requires_reviewers():
    # GitHub creates a missing environment unprotected and runs its job without waiting, so the job checks before anything else.
    push = blocks('github-actions.yml')['push']
    assert re.search(r'^    permissions:\n      actions: read\n    steps:\n      - name: ' + re.escape(REVIEWERS_STEP) + '\n', push, re.MULTILINE) is not None
    assert 'GH_TOKEN: ${{ github.token }}' in github_step(push, REVIEWERS_STEP)
    assert push.index(REVIEWERS_STEP) < push.index('actions/download-artifact@v4') < push.index('sha256sum -c') < push.index('uc procure push')


@pytest.mark.parametrize(('rules', 'passes'), [(['required_reviewers'], True), (['wait_timer', 'required_reviewers', 'branch_policy'], True), ([], False), (['wait_timer', 'branch_policy'], False)])
@pytest.mark.parametrize('layout', [{'indent': 2}, {'separators': (',', ':')}])
def test_the_reviewers_check_passes_only_with_required_reviewers(tmp_path, rules, passes, layout):
    # As GitHub's API answers: every rule has a type; a required_reviewers rule lists reviewers, each with its own type (User or Team).
    environment = {'id': 1, 'name': 'procurement', 'protection_rules': [{'id': n, 'type': rule, 'reviewers': [{'type': 'User', 'reviewer': {'login': 'alice'}}]} for n, rule in enumerate(rules)]}
    (tmp_path / 'answer.json').write_text(json.dumps(environment, **layout), encoding='utf-8')
    result = run_reviewers_check(tmp_path)
    assert (result.returncode == 0) is passes, result.stdout
    assert ('no required reviewers' in result.stdout) is not passes
    called = (tmp_path / 'curl-args').read_text(encoding='utf-8').splitlines()
    assert 'Authorization: Bearer token-1' in called  # pragma: allowlist secret
    assert called[-1] == 'https://api.github.example/repos/corp/uc-pipelines/environments/procurement'


@pytest.mark.parametrize('bypass', [True, False])
@pytest.mark.parametrize('layout', [{'indent': 2}, {'separators': (',', ':')}])
def test_the_reviewers_check_warns_but_carries_on_when_administrators_can_bypass_the_approval(tmp_path, bypass, layout):
    # GitHub's API answers with can_admins_bypass beside the protection rules; it is true unless the environment turns the bypass off.
    environment = {'id': 1, 'name': 'procurement', 'can_admins_bypass': bypass, 'protection_rules': [{'id': 2, 'prevent_self_review': False, 'type': 'required_reviewers', 'reviewers': [{'type': 'Team', 'reviewer': {'slug': 'uc-approvers'}}]}]}
    (tmp_path / 'answer.json').write_text(json.dumps(environment, **layout), encoding='utf-8')
    result = run_reviewers_check(tmp_path)
    assert result.returncode == 0, result.stdout
    assert (BYPASS_WARNING in result.stdout) is bypass
    if bypass:
        assert "'Allow administrators to bypass configured protection rules'" in result.stdout


def test_the_reviewers_check_stops_when_the_environment_cannot_be_read(tmp_path):
    result = run_reviewers_check(tmp_path)  # no answer: the stub curl fails as curl -f does on an HTTP error
    assert result.returncode != 0
    assert 'cannot read' in result.stdout


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
        for job, block in gitlab_jobs(name).items():
            assert 'resource_group: jenkins-update-center\n' in block, job
        assert 'resource_group' not in gitlab_default(name)
        # The procure job holds the lock until the child pipeline ends, so a child job waiting for it would wait forever.
        assert 'resource_group' not in code(GITLAB_CHILD)


@pytest.mark.parametrize('name', NAMES)
def test_checking_the_settings_starts_no_other_service(name):
    assert 'docker compose run --rm --no-deps uc config --check' in blocks(name)['check']


@pytest.mark.parametrize(('report', 'staged'), [pytest.param(STAGED, 'true', id='staged'), pytest.param(NOTHING_STAGED, 'false', id='nothing-staged'), pytest.param(None, 'false', id='no-report')])
def test_jenkins_records_whether_anything_was_staged_from_the_review_line(tmp_path, report, staged):
    stage = blocks('Jenkinsfile')['stage']
    found = JENKINS_STAGED.search(stage)
    assert found is not None
    assert stage.index('uc procure stage') < found.start()
    if report is not None:
        (tmp_path / 'stage-report.txt').write_text(report, encoding='utf-8')
    result = subprocess.run(['sh', '-c', found.group(1)], cwd=tmp_path, env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, check=False, timeout=30)
    assert (result.returncode, result.stdout) == (0, f'{staged}\n')


def test_jenkins_reads_the_stage_report_in_the_workspace():
    stage = blocks('Jenkinsfile')['stage']
    assert "tee '${env.WORKSPACE}/stage-report.txt'" in stage
    flag = JENKINS_STAGED.search(stage)
    assert flag is not None
    # Inside dir(params.UC_DIR) the relative stage-report.txt would be looked for in the deployment directory, which has none: nothing would ever count as staged.
    for opening in re.finditer(r'\bdir\([^)]*\) \{', stage):
        assert not opening.end() <= flag.start() < block_end(stage, opening.end())


def test_jenkins_skips_the_review_and_what_follows_when_nothing_was_staged():
    found = blocks('Jenkinsfile')
    for stage, block in found.items():
        when = JENKINS_WHEN.search(block)
        assert (when is not None) is (stage in AFTER_STAGING), stage
        if when is not None:
            assert block.index('when {') < block.index('steps {'), stage
            # Decided before the stage takes an agent: a run with nothing staged holds none.
            assert (when.group(1) is not None) is ('agent {' in block), stage


@pytest.mark.parametrize(('report', 'rc', 'staged'), REPORTS)
def test_the_github_stage_step_tells_the_push_job_whether_anything_was_staged(tmp_path, report, rc, staged):
    uc_dir, env = deployment(tmp_path, report, rc)
    (tmp_path / 'runner').mkdir()
    env |= {'RUNNER_TEMP': str(tmp_path / 'runner'), 'GITHUB_OUTPUT': str(tmp_path / 'output')}
    result = run_bash(tmp_path, github_script('stage', STAGE_STEP), env, uc_dir)
    assert result.returncode == rc, result.stdout
    assert (tmp_path / 'docker-args').read_text(encoding='utf-8').split() == ['compose', 'run', '--rm', 'uc', 'procure', 'stage']
    assert (tmp_path / 'output').read_text(encoding='utf-8') == f'staged={str(staged).lower()}\n'
    assert ('nothing was staged' in result.stdout) is not staged
    assert (tmp_path / 'runner' / 'stage-report.txt').read_text(encoding='utf-8') == report


def test_the_github_push_job_runs_only_when_something_was_staged():
    found = blocks('github-actions.yml')
    assert re.search(r'^    outputs:\n      staged: \$\{\{ steps\.stage\.outputs\.staged \}\}$', found['stage'], re.MULTILINE) is not None
    assert '\n        id: stage\n' in github_step(found['stage'], STAGE_STEP)
    # No status function in the if: GitHub adds success(), so a failed stage still stops the push. A skipped job asks no one to approve it.
    assert re.search(r"^    if: needs\.stage\.outputs\.staged == 'true'$", found['push'], re.MULTILINE) is not None
    # A job whose needs was skipped is skipped too, unless its own if: says otherwise.
    for job, before in (('generate', 'push'), ('verify', 'generate'), ('clean', 'verify')):
        assert f'\n    needs: {before}\n' in found[job], job
        assert re.search(r'^    if:', found[job], re.MULTILINE) is None, job


@pytest.mark.parametrize(('report', 'rc', 'staged'), REPORTS)
def test_the_gitlab_stage_job_chooses_the_child_pipeline(tmp_path, report, rc, staged):
    result, project = run_gitlab_stage_job(tmp_path, report, rc)
    assert result.returncode == rc, result.stdout
    assert (project / 'stage-report.txt').read_text(encoding='utf-8') == report
    assert ('nothing was staged' in result.stdout) is not staged
    child = (project / 'procure-pipeline.yml').read_text(encoding='utf-8')
    if staged:
        assert child == (PIPELINES / GITLAB_CHILD).read_text(encoding='utf-8')
        return
    # JSON is YAML too: one job, on the uc-host runner, that says nothing was staged and asks no one to approve anything.
    jobs = json.loads(child)
    assert len(jobs) == 1
    [(name, job)] = jobs.items()
    assert name not in GITLAB_KEYWORDS
    assert not name.startswith('.')
    assert sorted(job) == ['script', 'tags']
    assert job['tags'] == ['uc-host']
    assert all(isinstance(command, str) for command in job['script'])
    assert 'nothing was staged' in ' '.join(job['script'])


def test_the_gitlab_stage_job_passes_on_the_directory_it_used(tmp_path):
    # UC_DIR may be set under variables:, for the project, group or instance, or for the run: the stage job records the one it used.
    dotenv = GITLAB_DOTENV.search(gitlab_jobs('gitlab-ci.yml')['stage'])
    assert dotenv is not None
    result, project = run_gitlab_stage_job(tmp_path, STAGED, 0)
    assert result.returncode == 0, result.stdout
    assert (project / dotenv.group(1)).read_text(encoding='utf-8') == f'UC_DIR={tmp_path / "uc"}\n'


def test_every_gitlab_job_works_in_uc_dir_on_the_uc_host_runner():
    for file in FILES['gitlab-ci.yml']:
        default = gitlab_default(file)
        assert default.startswith('  tags: [uc-host]\n'), file
        assert gitlab_script(default, 'before_script') == ['set -o pipefail', 'cd "$UC_DIR"'], file
        for job, block in gitlab_jobs(file).items():
            assert re.search(r'^  (?:tags|before_script):', block, re.MULTILINE) is None, job


def test_the_gitlab_procure_job_starts_the_chosen_child_pipeline_and_holds_the_lock_until_it_ends():
    stage = gitlab_jobs('gitlab-ci.yml')['stage']
    assert re.search(r'^    paths: \[.*\bprocure-pipeline\.yml\b.*\]$', stage, re.MULTILINE) is not None
    procure = gitlab_jobs('gitlab-ci.yml')['procure']
    assert '\n  trigger:\n    include:\n      - artifact: procure-pipeline.yml\n        job: stage\n' in procure
    # depend: the procure job runs, so holds the resource group, until the child pipeline ends, the review included.
    assert '\n    strategy: depend\n' in procure
    assert '\n  resource_group: jenkins-update-center\n' in procure
    assert re.search(r'^  variables:\n    PARENT_PIPELINE_ID: \$CI_PIPELINE_ID$', procure, re.MULTILINE) is not None
    # needs: [stage] hands this job the stage job's dotenv report, and forwarding the pipeline variables passes its UC_DIR on to the child pipeline,
    # where it outranks the UC_DIR of variables: that the YAML variables carry: the child works in the directory the stage job used.
    assert '\n  needs: [stage]\n' in procure
    assert '\n    forward:\n      pipeline_variables: true\n' in procure
    # The YAML variables carry PARENT_PIPELINE_ID, which push needs to fetch the stage job's artifacts.
    assert 'yaml_variables: false' not in procure
    assert 'script:' not in procure
    # A child pipeline runs with CI_PIPELINE_SOURCE parent_pipeline: workflow rules copied from gitlab-ci.yml would stop it.
    assert 'workflow:' not in code(GITLAB_CHILD)
