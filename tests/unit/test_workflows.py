"""The GitHub Actions workflows' own logic: their scripts, and what ties their jobs.

zizmor checks their security (pinned actions, permissions, script injection, persisted
credentials) and actionlint their syntax, both as pre-commit hooks. What is left here is
behavior: each script runs with stub commands, as the runner would run it, plus the few
invariants no linter knows (docs/development/releasing.md).
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from shellrun import run_bash, stub

GITHUB = Path(__file__).resolve().parents[2] / 'github'
"""The repository's .github directory, which make test-unit mounts at /github."""
WORKFLOWS = GITHUB / 'workflows'
PATH = '/usr/bin:/bin'
REPOSITORY = {
    'GITHUB_SERVER_URL': 'https://github.com',
    'GITHUB_REPOSITORY': 'HaleTechCo/easy-update-center',
    'GITHUB_RUN_ID': '42',
}
"""What GitHub Actions sets for every step, as the scripts read it."""


def workflow_names() -> list[str]:
    """Return the file names of every workflow."""
    return sorted(path.name for path in WORKFLOWS.glob('*.yml'))


def load(name: str) -> dict:
    """Return a workflow as data, with its on: under 'on'.

    YAML 1.1 reads a bare on as true.
    """
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


def last_call(tmp_path: Path, command: str) -> str:
    """Return the last line a stub command logged."""
    return (tmp_path / f'{command}.log').read_text(encoding='utf-8').splitlines()[-1]


@pytest.mark.parametrize('name', workflow_names())
def test_every_workflow_runs_bash_with_pipefail(name):
    # GitHub runs `shell: bash` as bash -e -o pipefail; the tests below run scripts the
    # same way.
    assert load(name)['defaults'] == {'run': {'shell': 'bash'}}


def test_every_ci_checkout_takes_the_ref_it_is_asked_for():
    # The Release workflow runs CI on the tagged commit through this input.
    for job_name, job in jobs('ci.yml').items():
        for each in job.get('steps', []):
            if each.get('uses', '').startswith('actions/checkout@'):
                assert each['with']['ref'] == '${{ inputs.ref }}', job_name


def test_the_ci_check_needs_every_job_and_fails_unless_all_succeeded(tmp_path):
    # The one check the branch ruleset requires: a skipped job must not let a change
    # through.
    ci = jobs('ci.yml')
    gate = ci['ci']
    assert set(gate['needs']) == set(ci) - {'ci'}
    assert gate['if'] == 'always()'
    script = step(gate, 'Every job succeeded')['run']
    passing = {'RESULTS': 'success success success success', 'PATH': PATH}
    assert run_bash(tmp_path, script, passing, tmp_path).returncode == 0
    for results in (
        'success failure success success',
        'success success skipped success',
        'cancelled success success success',
    ):
        env = {'RESULTS': results, 'PATH': PATH}
        result = run_bash(tmp_path, script, env, tmp_path)
        assert result.returncode == 1, results
        assert '::error::' in result.stdout


def test_ci_needs_no_secrets():
    # Pull requests from forks get no secrets, so everything CI runs must pass without
    # them.
    assert 'secrets.' not in (WORKFLOWS / 'ci.yml').read_text(encoding='utf-8')


UPSTREAM_TAGS = ''.join(
    f'{"0" * 40}\trefs/tags/update-center2-{tag}\n'
    for tag in ('3.9', '3.18', '3.18.4', '3.19-rc1', '3.19')
)
"""What git ls-remote prints for update-center2's tags.

3.19 is the newest release; 3.19-rc1 is not a release.
"""
GIT_STUB = '#!/bin/sh\nprintf "%s" "$STUB_TAGS"\n'
MAIN_JAVA = 'src/main/java/io/jenkins/update_center/Main.java'
MAKE_STUB = f"""#!/bin/sh
echo "$*" >> "$STUB_DIR/make.log"
cat <<'EOF'
== files changed between 3.18.4 and 3.19
 {MAIN_JAVA} | 4 ++--
 1 file changed, 2 insertions(+), 2 deletions(-)
== files uc depends on
diff --git a/{MAIN_JAVA} b/{MAIN_JAVA}
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
    'release view') exit "${STUB_RELEASE_VIEW:-0}" ;;
esac
"""


def scheduled_step(job: str, name: str) -> dict:
    return step(jobs('scheduled.yml')[job], name)


@pytest.mark.parametrize(
    ('username', 'password', 'present'),
    [('user', 'secret', 'true'), ('', '', 'false'), ('user', '', 'false')],
)
def test_it_online_is_skipped_with_a_notice_without_the_community_account(
    tmp_path, username, password, present
):
    output = tmp_path / 'output'
    env = {
        'USERNAME': username,
        'PASSWORD': password,
        'GITHUB_OUTPUT': str(output),
        'PATH': PATH,
    }
    result = run_bash(
        tmp_path,
        scheduled_step('it-online', 'Check for the community account')['run'],
        env,
        tmp_path,
    )
    assert result.returncode == 0, result.stdout
    assert output.read_text(encoding='utf-8') == f'present={present}\n'
    assert ('::notice::it-online skipped' in result.stdout) is (present == 'false')
    for later in jobs('scheduled.yml')['it-online']['steps'][1:]:
        assert later['if'] == "steps.account.outputs.present == 'true'"


def run_upstream_watch(
    tmp_path: Path, pinned: str, issue: str = ''
) -> subprocess.CompletedProcess[str]:
    """Run upstream-watch.sh in a project pinning update-center2 at pinned.

    git, make and gh are stubs; issue is the "number STATE" line gh finds for the
    title, if any.
    """
    project = tmp_path / 'project'
    (project / 'uc').mkdir(parents=True)
    (project / 'uc' / 'Dockerfile').write_text(
        f'ARG UC2_VERSION={pinned}\nARG UC2_COMMIT=abc\n', encoding='utf-8'
    )
    for name, body in (('git', GIT_STUB), ('make', MAKE_STUB), ('gh', GH_STUB)):
        stubs = stub(tmp_path, name, body)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_TAGS': UPSTREAM_TAGS,
        'STUB_ISSUE': issue,
        **REPOSITORY,
    }
    script = (GITHUB / 'scripts' / 'upstream-watch.sh').read_text(encoding='utf-8')
    return run_bash(tmp_path, script, env, project)


def test_upstream_watch_opens_an_issue_for_a_newer_release(tmp_path):
    result = run_upstream_watch(tmp_path, '3.18.4')
    assert result.returncode == 0, result.stdout
    make_calls = (tmp_path / 'make.log').read_text(encoding='utf-8')
    assert make_calls == '-s upstream-diff TO=3.19\n'
    assert last_call(tmp_path, 'gh').startswith(
        'issue create --title update-center2 3.19 is available --body-file '
    )
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
    assert last_call(tmp_path, 'gh').startswith('issue edit 12 --body-file ')


def test_upstream_watch_leaves_a_closed_issue_for_that_release_alone(tmp_path):
    result = run_upstream_watch(tmp_path, '3.18.4', issue='12 CLOSED')
    assert result.returncode == 0, result.stdout
    assert 'issue #12 for update-center2 3.19 is closed' in result.stdout
    calls = (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()
    writes = [call for call in calls if call.startswith(('issue create', 'issue edit'))]
    assert writes == []
    assert '--state all' in calls[0]


def test_upstream_watch_does_nothing_when_the_pin_is_the_newest_release(tmp_path):
    result = run_upstream_watch(tmp_path, '3.19')
    assert result.returncode == 0, result.stdout
    assert 'update-center2 3.19 is the newest release' in result.stdout
    assert not (tmp_path / 'make.log').exists()
    assert not (tmp_path / 'gh.log').exists()


def grype_match(cve: str, severity: str, state: str = 'fixed') -> dict:
    """Return one match of a Grype JSON report: cve, of severity, in openssl 3.5.5.

    It is fixed in 3.5.6 when state is fixed.
    """
    versions = ['3.5.6'] if state == 'fixed' else []
    return {
        'vulnerability': {
            'id': cve,
            'severity': severity,
            'fix': {'state': state, 'versions': versions},
        },
        'artifact': {'name': 'openssl', 'version': '3.5.5'},
    }


FINDINGS = [
    grype_match('CVE-2026-1', 'Critical'),
    grype_match('CVE-2026-2', 'High'),
    grype_match('CVE-2026-2', 'High'),
    grype_match('CVE-2026-3', 'Medium'),
    grype_match('CVE-2026-4', 'High', state='not-fixed'),
]
"""Two vulnerabilities worth a release, one below High, and one with no fix.

CVE-2026-2 is matched twice.
"""
SCAN_ISSUE = 'Fixable vulnerabilities in the published images'


def run_image_scan(
    tmp_path: Path, uc_matches: list[dict], web_matches: list[dict], issue: str = ''
) -> subprocess.CompletedProcess[str]:
    """Run image-scan.sh on Grype reports for the uc and web images, with a stub gh.

    issue is the number of the open issue gh finds, if any.
    """
    arguments = []
    for image, matches in (('uc', uc_matches), ('web', web_matches)):
        report = tmp_path / f'{image}.json'
        report.write_text(json.dumps({'matches': matches}), encoding='utf-8')
        arguments.append(
            f'ghcr.io/haletechco/easy-update-center-{image}:0.1.0={report}'
        )
    stubs = stub(tmp_path, 'gh', GH_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_ISSUE': issue,
        **REPOSITORY,
    }
    script = (GITHUB / 'scripts' / 'image-scan.sh').read_text(encoding='utf-8')
    return run_bash(tmp_path, f'set -- {" ".join(arguments)}\n{script}', env, tmp_path)


def test_image_scan_issue_lists_high_and_critical_vulnerabilities_with_a_fix(tmp_path):
    result = run_image_scan(tmp_path, FINDINGS, [])
    assert result.returncode == 0, result.stdout
    expected = f'issue create --title {SCAN_ISSUE} --body-file '
    assert last_call(tmp_path, 'gh').startswith(expected)
    body = (tmp_path / 'body.md').read_text(encoding='utf-8')
    assert 'found 2 High or Critical vulnerabilities that have a fix' in body
    assert '| CVE-2026-1 | Critical | openssl 3.5.5 | 3.5.6 |' in body
    assert body.count('CVE-2026-2') == 1
    assert 'CVE-2026-3' not in body
    assert 'CVE-2026-4' not in body
    assert '### ghcr.io/haletechco/easy-update-center-web:0.1.0\n\nNone.' in body
    assert 'https://github.com/HaleTechCo/easy-update-center/actions/runs/42' in body


def test_image_scan_updates_the_open_issue(tmp_path):
    result = run_image_scan(tmp_path, [], FINDINGS, issue='12')
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'gh').startswith('issue edit 12 --body-file ')
    body = (tmp_path / 'body.md').read_text(encoding='utf-8')
    assert '| CVE-2026-1 | Critical |' in body


def test_image_scan_closes_the_open_issue_once_a_scan_finds_none(tmp_path):
    medium = [grype_match('CVE-2026-3', 'Medium')]
    result = run_image_scan(tmp_path, medium, [], issue='12')
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'gh') == (
        'issue close 12 --comment The scan in '
        'https://github.com/HaleTechCo/easy-update-center/actions/runs/42 '
        'found none: closing.'
    )


def test_image_scan_does_nothing_when_it_finds_none_and_no_issue_is_open(tmp_path):
    result = run_image_scan(tmp_path, [], [])
    assert result.returncode == 0, result.stdout
    assert 'no High or Critical vulnerability with a fix' in result.stdout
    calls = (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()
    assert [call for call in calls if not call.startswith('issue list')] == []


def release_step(job: str, name: str) -> dict:
    return step(jobs('release.yml')[job], name)


CHECK_STUBS = {
    'git': '#!/bin/sh\necho "$*" >> "$STUB_DIR/git.log"\nexit "$STUB_ON_MAIN"\n',
    'python3': (
        '#!/bin/sh\necho "$*" >> "$STUB_DIR/python3.log"\n'
        'printf "version=0.1.0\\nlatest=true\\nminor_latest=true\\n"\n'
    ),
}
"""Stubs for the check step.

git answers merge-base with STUB_ON_MAIN (0: on main); python3 prints release.py's
output.
"""


def run_release_check(
    tmp_path: Path, ref: str, event: str = 'push', *, on_main: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run the Release workflow's check step for a run on ref, started by event."""
    for name, body in CHECK_STUBS.items():
        stubs = stub(tmp_path, name, body)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_ON_MAIN': '0' if on_main else '1',
        'GITHUB_OUTPUT': str(tmp_path / 'output'),
        'GITHUB_REF': ref,
        'GITHUB_REF_TYPE': 'tag' if ref.startswith('refs/tags/') else 'branch',
        'GITHUB_REF_NAME': ref.rsplit('/', 1)[-1],
        'GITHUB_SHA': 'abc123',
        'EVENT': event,
    }
    script = release_step('check', 'Check the tag')['run']
    return run_bash(tmp_path, script, env, tmp_path)


def test_the_release_checks_a_pushed_tag_on_main(tmp_path):
    result = run_release_check(tmp_path, 'refs/tags/v0.1.0')
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'git') == 'merge-base --is-ancestor abc123 origin/main'
    assert last_call(tmp_path, 'python3') == 'tools/release.py check 0.1.0'
    output = (tmp_path / 'output').read_text(encoding='utf-8')
    assert output == 'version=0.1.0\nlatest=true\nminor_latest=true\n'


def test_a_run_by_hand_on_a_tag_republishes_it(tmp_path):
    result = run_release_check(tmp_path, 'refs/tags/v0.1.0', 'workflow_dispatch')
    assert result.returncode == 0, result.stdout
    expected = 'tools/release.py check 0.1.0 --republish'
    assert last_call(tmp_path, 'python3') == expected


def test_the_release_refuses_a_run_on_a_branch(tmp_path):
    result = run_release_check(tmp_path, 'refs/heads/main', 'workflow_dispatch')
    assert result.returncode == 1
    expected = '::error::run the Release workflow on a release tag, not refs/heads/main'
    assert expected in result.stdout
    assert not (tmp_path / 'python3.log').exists()


def test_the_release_refuses_a_tag_that_is_not_on_main(tmp_path):
    result = run_release_check(tmp_path, 'refs/tags/v0.1.0', on_main=False)
    assert result.returncode == 1
    assert '::error::v0.1.0 is not on main' in result.stdout
    assert not (tmp_path / 'python3.log').exists()


DOCKER_STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/docker.log"
case "$*" in
    'buildx imagetools inspect'*) echo 'sha256:1111' ;;
esac
"""


@pytest.mark.parametrize(
    ('latest', 'minor_latest', 'tags'),
    [
        ('true', 'true', ['0.2.1', '0.2', 'latest']),
        ('false', 'true', ['0.2.1', '0.2']),
        ('false', 'false', ['0.2.1']),
    ],
)
def test_the_manifest_lists_move_latest_and_the_minor_tag_only_for_the_highest_release(
    tmp_path, latest, minor_latest, tags
):
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
    script = release_step('manifests', 'Create the multi-architecture manifest lists')
    result = run_bash(tmp_path, script['run'], env, tmp_path)
    assert result.returncode == 0, result.stdout
    creates = [
        line
        for line in (tmp_path / 'docker.log').read_text(encoding='utf-8').splitlines()
        if line.startswith('buildx imagetools create')
    ]
    assert len(creates) == 2
    for image, line in zip(('uc', 'web'), creates, strict=True):
        repo = f'ghcr.io/haletechco/easy-update-center-{image}'
        assert re.findall(r'--tag (\S+)', line) == [f'{repo}:{tag}' for tag in tags]
        assert line.endswith(f'{repo}@sha256:aaaa {repo}@sha256:bbbb')
    digests = output.read_text(encoding='utf-8')
    assert digests == 'uc-digest=sha256:1111\nweb-digest=sha256:1111\n'


def test_the_release_refuses_an_asset_of_2_gib_naming_it(tmp_path):
    script = release_step('release', 'Check the asset sizes')['run']
    (tmp_path / 'assets').mkdir()
    fits = tmp_path / 'assets' / 'fits.tar'
    subprocess.run(['truncate', '--size=2047M', str(fits)], check=True)
    assert run_bash(tmp_path, script, {'PATH': PATH}, tmp_path).returncode == 0
    too_big = tmp_path / 'assets' / 'too-big.tar'
    subprocess.run(['truncate', '--size=2G', str(too_big)], check=True)
    result = run_bash(tmp_path, script, {'PATH': PATH}, tmp_path)
    assert result.returncode == 1
    assert 'assets/too-big.tar' in result.stdout
    assert 'fits.tar' not in result.stdout


def run_publish(tmp_path: Path, *, exists: bool) -> subprocess.CompletedProcess[str]:
    """Run the step that publishes the GitHub Release, whose Release exists or not."""
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'bundle.tar').touch()
    notes = '#!/bin/sh\necho "$*" >> "$STUB_DIR/python3.log"\necho "- A change."\n'
    stub(tmp_path, 'python3', notes)
    stubs = stub(tmp_path, 'gh', GH_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_RELEASE_VIEW': '0' if exists else '1',
        'RUNNER_TEMP': str(tmp_path),
        'VERSION': '0.1.0',
        'LATEST': 'true',
    }
    script = release_step('release', 'Publish the GitHub Release')['run']
    return run_bash(tmp_path, script, env, tmp_path)


def test_the_first_run_on_a_tag_creates_its_release(tmp_path):
    result = run_publish(tmp_path, exists=False)
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'python3') == 'tools/release.py notes 0.1.0'
    assert last_call(tmp_path, 'gh') == (
        f'release create v0.1.0 --verify-tag --title 0.1.0 --notes-file '
        f'{tmp_path}/notes.md --latest=true assets/bundle.tar'
    )
    assert (tmp_path / 'notes.md').read_text(encoding='utf-8') == '- A change.\n'


def test_a_run_on_a_released_tag_replaces_its_notes_and_assets(tmp_path):
    result = run_publish(tmp_path, exists=True)
    assert result.returncode == 0, result.stdout
    calls = (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()
    assert calls[1:] == [
        f'release edit v0.1.0 --notes-file {tmp_path}/notes.md',
        'release upload v0.1.0 assets/bundle.tar --clobber',
    ]
