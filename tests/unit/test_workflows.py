"""The GitHub Actions workflows (.github/workflows): pinned actions, least permissions, the CI gate, the scheduled jobs and the release (docs/development/releasing.md)."""

import itertools
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from shellrun import run_bash, stub

GITHUB = Path(__file__).resolve().parents[2] / 'github'
"""The repository's .github directory, which make test-unit mounts at /github."""
WORKFLOWS = GITHUB / 'workflows'
PINNED = re.compile(r'^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$')
USES = re.compile(r'^\s*(?:-\s+)?uses:\s*(\S+)(.*)$', re.MULTILINE)
EXTRA_PERMISSIONS: dict[tuple[str, str], dict[str, str]] = {
    ('scheduled.yml', 'upstream-watch'): {'contents': 'read', 'issues': 'write'},
    ('release.yml', 'images'): {'contents': 'read', 'packages': 'write'},
    ('release.yml', 'manifests'): {'contents': 'read', 'packages': 'write'},
    ('release.yml', 'sign'): {'contents': 'read', 'packages': 'write', 'id-token': 'write', 'attestations': 'write'},
    ('release.yml', 'bundle'): {'contents': 'read', 'packages': 'read'},
    ('release.yml', 'release'): {'contents': 'write', 'id-token': 'write'},
}
"""Every job that declares permissions, with exactly the ones it needs; any other job inherits contents: read."""
WRITES_CONTENTS = {('release.yml', 'release')}
"""The only job whose workflow token may write contents (it creates the GitHub Release). prepare pushes with the release App's token instead: the App is the rulesets' only bypass actor, and its key lives in the release environment, which only main can use."""
CHECKOUT_WITH_CREDENTIALS = {('release.yml', 'prepare')}
"""The only jobs whose checkout keeps a token, for a git push."""
TEST_TARGETS = ['test-unit', 'test-web', 'test-routing', 'test-setup', 'compose-check']
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


UPSTREAM_TAGS = ''.join(f'{"0" * 40}\trefs/tags/update-center2-{tag}\n' for tag in ('3.9', '3.18', '3.18.4', '3.19-rc1', '3.19'))
"""What git ls-remote prints for update-center2's tags: 3.19 is the newest release, 3.19-rc1 is not a release."""
GIT_STUB = '#!/bin/sh\nprintf "%s" "$STUB_TAGS"\n'
MAKE_STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/make.log"
cat <<'EOF'
== files changed between 3.18.4 and 3.19
 src/main/java/io/jenkins/update_center/Main.java | 4 ++--
 1 file changed, 2 insertions(+), 2 deletions(-)
== files uc depends on
diff --git a/src/main/java/io/jenkins/update_center/Main.java b/src/main/java/io/jenkins/update_center/Main.java
== patches apply to 3.19
EOF
"""
GH_STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/gh.log"
case "$1 $2" in
    'issue list') printf '%s' "$STUB_ISSUE" ;;
    'issue create'|'issue edit')
        while [ $# -gt 0 ]; do
            if [ "$1" = --body-file ]; then cp "$2" "$STUB_DIR/body.md"; fi
            shift
        done ;;
esac
"""


def scheduled_step(job: str, name: str) -> dict:
    return step(jobs('scheduled.yml')[job], name)


def test_scheduled_runs_weekly_and_by_hand():
    on = load('scheduled.yml')['on']
    assert set(on) == {'schedule', 'workflow_dispatch'}
    _minute, _hour, day, month, weekday = on['schedule'][0]['cron'].split()
    assert (day, month) == ('*', '*')
    assert weekday.isdigit()


@pytest.mark.parametrize(('username', 'password', 'present'), [('user', 'secret', 'true'), ('', '', 'false'), ('user', '', 'false')])
def test_it_online_is_skipped_with_a_notice_without_the_community_account(tmp_path, username, password, present):
    output = tmp_path / 'output'
    env = {'USERNAME': username, 'PASSWORD': password, 'GITHUB_OUTPUT': str(output), 'PATH': PATH}
    result = run_bash(tmp_path, scheduled_step('it-online', 'Check for the community account')['run'], env, tmp_path)
    assert result.returncode == 0, result.stdout
    assert output.read_text(encoding='utf-8') == f'present={present}\n'
    assert ('::notice::it-online skipped' in result.stdout) is (present == 'false')
    for later in jobs('scheduled.yml')['it-online']['steps'][1:]:
        assert later['if'] == "steps.account.outputs.present == 'true'"


def run_upstream_watch(tmp_path: Path, pinned: str, issue: str = '') -> subprocess.CompletedProcess[str]:
    """Run upstream-watch.sh in a project pinning update-center2 at pinned, with stub git, make and gh; issue is the "number STATE" line gh finds for the title, if any."""
    project = tmp_path / 'project'
    (project / 'uc').mkdir(parents=True)
    (project / 'uc' / 'Dockerfile').write_text(f'ARG UC2_VERSION={pinned}\nARG UC2_COMMIT=abc\n', encoding='utf-8')
    for name, body in (('git', GIT_STUB), ('make', MAKE_STUB), ('gh', GH_STUB)):
        stubs = stub(tmp_path, name, body)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_TAGS': UPSTREAM_TAGS,
        'STUB_ISSUE': issue,
        'GITHUB_SERVER_URL': 'https://github.com',
        'GITHUB_REPOSITORY': 'HaleTechCo/easy-update-center',
        'GITHUB_RUN_ID': '42',
    }
    return run_bash(tmp_path, (GITHUB / 'scripts' / 'upstream-watch.sh').read_text(encoding='utf-8'), env, project)


def test_upstream_watch_opens_an_issue_for_a_newer_release(tmp_path):
    result = run_upstream_watch(tmp_path, '3.18.4')
    assert result.returncode == 0, result.stdout
    assert (tmp_path / 'make.log').read_text(encoding='utf-8') == '-s upstream-diff TO=3.19\n'
    assert (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()[-1].startswith('issue create --title update-center2 3.19 is available --body-file ')
    body = (tmp_path / 'body.md').read_text(encoding='utf-8')
    assert 'uc/Dockerfile pins 3.18.4' in body
    assert 'Main.java | 4 ++--' in body
    assert '== patches apply to 3.19' in body
    assert 'diff --git' not in body  # the full diff stays in the run's log
    assert 'diff --git' in result.stdout
    assert 'https://github.com/HaleTechCo/easy-update-center/actions/runs/42' in body


def test_upstream_watch_updates_the_open_issue_for_that_release(tmp_path):
    result = run_upstream_watch(tmp_path, '3.18.4', issue='12 OPEN')
    assert result.returncode == 0, result.stdout
    assert (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()[-1].startswith('issue edit 12 --body-file ')


def test_upstream_watch_leaves_a_closed_issue_for_that_release_alone(tmp_path):
    result = run_upstream_watch(tmp_path, '3.18.4', issue='12 CLOSED')
    assert result.returncode == 0, result.stdout
    assert 'issue #12 for update-center2 3.19 is closed' in result.stdout
    calls = (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()
    assert [call for call in calls if call.startswith(('issue create', 'issue edit'))] == []
    assert '--state all' in calls[0]


def test_upstream_watch_does_nothing_when_the_pin_is_the_newest_release(tmp_path):
    result = run_upstream_watch(tmp_path, '3.19')
    assert result.returncode == 0, result.stdout
    assert 'update-center2 3.19 is the newest release' in result.stdout
    assert not (tmp_path / 'make.log').exists()
    assert not (tmp_path / 'gh.log').exists()


def test_dependabot_updates_actions_images_and_lint_tools_weekly_in_one_pull_request_each():
    config = yaml.safe_load((GITHUB / 'dependabot.yml').read_text(encoding='utf-8'))
    updates = {update['package-ecosystem']: update for update in config['updates']}
    assert set(updates) == {'github-actions', 'docker', 'pip'}
    assert updates['docker']['directories'] == ['/uc', '/web']
    for update in updates.values():
        assert update['schedule']['interval'] == 'weekly'
        assert [group['patterns'] for group in update['groups'].values()] == [['*']]


RELEASE_ORDER = ['check', 'ci', 'prepare', 'images', 'manifests', 'sign', 'bundle', 'release']
TOOLS = Path(__file__).resolve().parents[2] / 'tools'
DOCKER_STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/docker.log"
case "$*" in
    'buildx imagetools inspect'*) echo 'sha256:1111' ;;
esac
"""


def release_job(name: str) -> dict:
    return jobs('release.yml')[name]


def test_the_release_runs_by_hand_with_a_version_and_publish_only():
    inputs = load('release.yml')['on']['workflow_dispatch']['inputs']
    assert set(load('release.yml')['on']) == {'workflow_dispatch'}
    assert inputs['version']['required'] is True
    assert (inputs['publish-only']['type'], inputs['publish-only']['default']) == ('boolean', False)


def test_release_jobs_run_in_order_each_after_the_one_before():
    release = jobs('release.yml')
    assert list(release) == RELEASE_ORDER
    for before, job in itertools.pairwise(RELEASE_ORDER):
        needs = release[job]['needs']
        assert before in ([needs] if isinstance(needs, str) else needs), job


def test_prepare_is_skipped_for_publish_only_and_the_jobs_after_it_still_run():
    assert release_job('prepare')['if'] == '${{ !inputs.publish-only }}'
    for job in RELEASE_ORDER[RELEASE_ORDER.index('images') :]:
        assert release_job(job)['if'] == '${{ !failure() && !cancelled() }}', job


def test_ci_runs_on_the_commit_being_released():
    ci = release_job('ci')
    assert ci['uses'] == './.github/workflows/ci.yml'
    assert ci['with']['ref'] == "${{ inputs.publish-only && format('refs/tags/v{0}', inputs.version) || github.sha }}"


def test_images_and_bundles_are_built_natively_for_both_architectures():
    images = release_job('images')['strategy']['matrix']['include']
    assert {(each['runner'], each['platform']) for each in images} == {('ubuntu-24.04', 'linux/amd64'), ('ubuntu-24.04-arm', 'linux/arm64')}
    bundles = release_job('bundle')['strategy']['matrix']['include']
    assert {(each['runner'], each['arch']) for each in bundles} == {('ubuntu-24.04', 'amd64'), ('ubuntu-24.04-arm', 'arm64')}


@pytest.mark.parametrize(('ref', 'publish_only', 'refused'), [('refs/heads/feature', 'false', True), ('refs/heads/main', 'false', False), ('refs/heads/main', 'true', False)])
def test_the_release_runs_only_on_main_and_passes_publish_only_on(tmp_path, ref, publish_only, refused):
    stubs = stub(tmp_path, 'python3', '#!/bin/sh\necho "$*" >> "$STUB_DIR/python3.log"\necho latest=true\n')
    output = tmp_path / 'output'
    env = {'PATH': f'{stubs}:{PATH}', 'STUB_DIR': str(tmp_path), 'GITHUB_REF': ref, 'GITHUB_OUTPUT': str(output), 'VERSION': '0.1.0', 'PUBLISH_ONLY': publish_only}
    result = run_bash(tmp_path, step(release_job('check'), 'Check the version')['run'], env, tmp_path)
    if refused:
        assert result.returncode == 1
        assert '::error::run the Release workflow on main, not refs/heads/feature' in result.stdout
        assert not (tmp_path / 'python3.log').exists()
    else:
        assert result.returncode == 0, result.stdout
        expected = 'tools/release.py check 0.1.0' + (' --publish-only' if publish_only == 'true' else '')
        assert (tmp_path / 'python3.log').read_text(encoding='utf-8') == f'{expected}\n'
        assert output.read_text(encoding='utf-8') == 'latest=true\n'


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def release_repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Make an origin whose main holds CHANGELOG.md and tools/release.py, a second clone to move main with, and the checkout actions/checkout leaves; return all three."""
    origin, other, work = tmp_path / 'origin.git', tmp_path / 'other', tmp_path / 'work'
    git('init', '-q', '--bare', '--initial-branch=main', str(origin), cwd=tmp_path)
    git('clone', '-q', str(origin), str(other), cwd=tmp_path)
    (other / 'tools').mkdir()
    shutil.copy(TOOLS / 'release.py', other / 'tools' / 'release.py')
    (other / 'CHANGELOG.md').write_text('# Changelog\n\n## Unreleased\n\n### Added\n\n- The first release.\n', encoding='utf-8')
    git('add', '-A', cwd=other)
    git('commit', '-q', '-m', 'Start', cwd=other)
    git('push', '-q', 'origin', 'HEAD:main', cwd=other)
    git('clone', '-q', str(origin), str(work), cwd=tmp_path)
    return origin, other, work


APP_SLUG = 'easy-update-center-release'
GH_USER_STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/gh.log"
[ "$*" = 'api /users/easy-update-center-release[bot] --jq .id' ] && echo 4242
"""


def test_prepare_pushes_with_the_release_apps_token_from_the_release_environment():
    prepare = release_job('prepare')
    assert prepare['environment'] == 'release'
    app = step(prepare, 'Get the release App token')
    assert app['uses'].startswith('actions/create-github-app-token@')
    assert app['with'] == {'client-id': '${{ vars.RELEASE_APP_CLIENT_ID }}', 'private-key': '${{ secrets.RELEASE_APP_PRIVATE_KEY }}', 'permission-contents': 'write'}
    checkout = next(each for each in prepare['steps'] if each.get('uses', '').startswith('actions/checkout@'))
    assert checkout['with'] == {'token': '${{ steps.app.outputs.token }}'}
    assert step(prepare, 'Commit the release and tag it')['env'] == {'APP_SLUG': '${{ steps.app.outputs.app-slug }}', 'GH_TOKEN': '${{ steps.app.outputs.token }}'}


def run_prepare(tmp_path: Path, work: Path) -> subprocess.CompletedProcess[str]:
    stubs = stub(tmp_path, 'gh', GH_USER_STUB)
    script = step(release_job('prepare'), 'Commit the release and tag it')['run']
    env = {'VERSION': '0.1.0', 'APP_SLUG': APP_SLUG, 'GH_TOKEN': 'app-token', 'PATH': f'{stubs}:{PATH}', 'STUB_DIR': str(tmp_path), 'HOME': str(tmp_path)}
    return run_bash(tmp_path, script, env, work)


def test_prepare_pushes_the_release_commit_and_its_tag_together(tmp_path):
    origin, _, work = release_repository(tmp_path)
    result = run_prepare(tmp_path, work)
    assert result.returncode == 0, result.stdout
    assert git('log', '-1', '--format=%s|%an|%ae', 'main', cwd=origin) == f'Release 0.1.0|{APP_SLUG}[bot]|4242+{APP_SLUG}[bot]@users.noreply.github.com\n'
    assert git('rev-parse', 'v0.1.0^{commit}', cwd=origin) == git('rev-parse', 'main', cwd=origin)
    assert '## [0.1.0] - ' in git('show', 'main:CHANGELOG.md', cwd=origin)


def test_prepare_pushes_nothing_when_main_moved_after_the_run_started(tmp_path):
    origin, other, work = release_repository(tmp_path)
    (other / 'meanwhile.txt').write_text('merged during the run\n', encoding='utf-8')
    git('add', '-A', cwd=other)
    git('commit', '-q', '-m', 'Meanwhile', cwd=other)
    git('push', '-q', 'origin', 'HEAD:main', cwd=other)
    result = run_prepare(tmp_path, work)
    assert result.returncode != 0
    assert git('tag', '--list', cwd=origin) == ''
    assert git('log', '-1', '--format=%s', 'main', cwd=origin) == 'Meanwhile\n'


@pytest.mark.parametrize(('latest', 'minor_latest', 'tags'), [('true', 'true', ['0.2.1', '0.2', 'latest']), ('false', 'true', ['0.2.1', '0.2']), ('false', 'false', ['0.2.1'])])
def test_the_manifest_lists_move_latest_and_the_minor_tag_only_for_the_highest_release(tmp_path, latest, minor_latest, tags):
    for image in ('uc', 'web'):
        (tmp_path / 'digests' / image).mkdir(parents=True)
        for digest in ('aaaa', 'bbbb'):
            (tmp_path / 'digests' / image / digest).touch()
    stubs = stub(tmp_path, 'docker', DOCKER_STUB)
    output = tmp_path / 'output'
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'VERSION': '0.2.1',
        'LATEST': latest,
        'MINOR_LATEST': minor_latest,
        'GITHUB_OUTPUT': str(output),
        'UC_REPO': 'ghcr.io/haletechco/easy-update-center-uc',
        'WEB_REPO': 'ghcr.io/haletechco/easy-update-center-web',
        'SOURCE_URL': 'https://github.com/HaleTechCo/easy-update-center',
    }
    result = run_bash(tmp_path, step(release_job('manifests'), 'Create the multi-architecture manifest lists')['run'], env, tmp_path)
    assert result.returncode == 0, result.stdout
    creates = [line for line in (tmp_path / 'docker.log').read_text(encoding='utf-8').splitlines() if line.startswith('buildx imagetools create')]
    assert len(creates) == 2
    for image, line in zip(('uc', 'web'), creates, strict=True):
        repo = f'ghcr.io/haletechco/easy-update-center-{image}'
        assert re.findall(r'--tag (\S+)', line) == [f'{repo}:{tag}' for tag in tags]
        assert line.endswith(f'{repo}@sha256:aaaa {repo}@sha256:bbbb')
    assert output.read_text(encoding='utf-8') == 'uc-digest=sha256:1111\nweb-digest=sha256:1111\n'


def test_the_release_refuses_an_asset_of_2_gib_naming_it(tmp_path):
    script = step(release_job('release'), 'Check the asset sizes')['run']
    (tmp_path / 'assets').mkdir()
    subprocess.run(['truncate', '--size=2047M', str(tmp_path / 'assets' / 'fits.tar')], check=True)
    assert run_bash(tmp_path, script, {'PATH': PATH}, tmp_path).returncode == 0
    subprocess.run(['truncate', '--size=2G', str(tmp_path / 'assets' / 'too-big.tar')], check=True)
    result = run_bash(tmp_path, script, {'PATH': PATH}, tmp_path)
    assert result.returncode == 1
    assert 'assets/too-big.tar' in result.stdout
    assert 'fits.tar' not in result.stdout


def test_the_release_creates_the_github_release_only_after_the_size_check():
    names = [each.get('name') for each in release_job('release')['steps']]
    assert names.index('Check the asset sizes') < names.index('Write and sign SHA256SUMS') < names.index('Publish the GitHub Release')
