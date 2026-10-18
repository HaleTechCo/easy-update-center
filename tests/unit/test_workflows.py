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
esac
"""


def scheduled_step(job: str, name: str) -> dict:
    return step(jobs('scheduled.yml')[job], name)


@pytest.mark.parametrize(
    ('username', 'password', 'present'),
    [
        ('user', 'secret', 'true'),
        ('', '', 'false'),
        ('user', '', 'false'),
        ('', 'secret', 'false'),
    ],
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
    # only an open issue is updated or closed: a closed one stays closed
    first = (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()[0]
    assert first.startswith('issue list --state open ')
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


def test_ci_pulls_the_images_under_test_and_names_them_as_make_expects(tmp_path):
    docker = '#!/bin/sh\necho "$*" >> "$STUB_DIR/docker.log"\n'
    stubs = stub(tmp_path, 'docker', docker)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'UC_IMAGE_UNDER_TEST': 'ghcr.io/x/uc@sha256:1111',
        'WEB_IMAGE_UNDER_TEST': 'ghcr.io/x/web@sha256:2222',
    }
    script = step(jobs('ci.yml')['images'], 'Pull the images under test')['run']
    result = run_bash(tmp_path, script, env, tmp_path)
    assert result.returncode == 0, result.stdout
    assert (tmp_path / 'docker.log').read_text(encoding='utf-8').splitlines() == [
        'pull --platform linux/amd64 ghcr.io/x/uc@sha256:1111',
        'pull --platform linux/amd64 ghcr.io/x/web@sha256:2222',
        'tag ghcr.io/x/uc@sha256:1111 easy-update-center/uc:local',
        'tag ghcr.io/x/web@sha256:2222 easy-update-center/web:local',
    ]


@pytest.mark.parametrize(
    ('uc', 'web', 'ok'),
    [
        ('', '', True),
        ('ghcr.io/x/uc@sha256:1111', 'ghcr.io/x/web@sha256:2222', True),
        ('ghcr.io/x/uc@sha256:1111', '', False),
        ('', 'ghcr.io/x/web@sha256:2222', False),
    ],
    ids=['neither', 'both', 'uc-only', 'web-only'],
)
def test_ci_refuses_one_image_under_test_without_the_other(tmp_path, uc, web, ok):
    first = jobs('ci.yml')['images']['steps'][0]
    assert first.get('name') == 'Check the image inputs'
    env = {'PATH': PATH, 'UC_IMAGE_UNDER_TEST': uc, 'WEB_IMAGE_UNDER_TEST': web}
    result = run_bash(tmp_path, first['run'], env, tmp_path)
    assert (result.returncode == 0) is ok, result.stdout
    assert ('::error::give both uc-image and web-image' in result.stdout) is not ok


def test_ci_keeps_the_images_for_the_release_s_re_runs():
    # The Release workflow's CI jobs download this artifact, and its failed jobs can be
    # re-run for 7 days.
    upload = next(
        each
        for each in jobs('ci.yml')['images']['steps']
        if each.get('uses', '').startswith('actions/upload-artifact@')
    )
    assert upload['with']['retention-days'] == "${{ inputs.uc-image != '' && 7 || 1 }}"


def test_only_zizmor_gets_the_token():
    lint = jobs('ci.yml')['lint']
    with_token = [
        each.get('name')
        for each in lint['steps']
        if 'GH_TOKEN' in (each.get('env') or {})
    ]
    assert with_token == ['zizmor']
    assert step(lint, 'pre-commit')['env'] == {'SKIP': 'zizmor'}


def release_step(job: str, name: str) -> dict:
    return step(jobs('release.yml')[job], name)


CHECK_STUBS = {
    'git': '#!/bin/sh\necho "$*" >> "$STUB_DIR/git.log"\nexit "$STUB_ON_MAIN"\n',
    'python3': (
        '#!/bin/sh\necho "$*" >> "$STUB_DIR/python3.log"\n'
        'printf "version=0.1.0\\nprerelease=false\\n"\n'
    ),
}
"""Stubs for the check step.

git answers merge-base with STUB_ON_MAIN (0: on main); python3 prints release.py's
output.
"""


def run_release_check(
    tmp_path: Path, ref: str, *, on_main: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run the Release workflow's check step for a push of the tag ref."""
    for name, body in CHECK_STUBS.items():
        stubs = stub(tmp_path, name, body)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_ON_MAIN': '0' if on_main else '1',
        'GITHUB_OUTPUT': str(tmp_path / 'output'),
        'GITHUB_REF_NAME': ref.rsplit('/', 1)[-1],
        'GITHUB_SHA': 'abc123',
    }
    script = release_step('check', 'Check the tag')['run']
    return run_bash(tmp_path, script, env, tmp_path)


def test_each_release_job_needs_the_jobs_whose_results_it_uses():
    needs = {
        name: {need} if isinstance(need := job.get('needs', []), str) else set(need)
        for name, job in jobs('release.yml').items()
    }
    assert needs == {
        'check': set(),
        'images': {'check'},
        'index': {'check', 'images'},
        'ci': {'check', 'index'},
        'sign': {'check', 'index', 'ci'},
        'bundle': {'check', 'index', 'sign'},
        'draft': {'check', 'sign', 'bundle'},
        'publish': {'check', 'index', 'draft'},
    }


def test_the_release_runs_ci_on_the_images_index_pushed():
    ci = jobs('release.yml')['ci']
    assert ci['uses'] == './.github/workflows/ci.yml'
    assert ci['with'] == {
        'uc-image': UC + '@${{ needs.index.outputs.uc-digest }}',
        'web-image': WEB + '@${{ needs.index.outputs.web-digest }}',
    }
    assert ci['permissions'] == {'contents': 'read', 'packages': 'read'}


def test_the_bundle_holds_the_signed_digests(tmp_path):
    bundle = jobs('release.yml')['bundle']
    assert bundle['env']['UC_DIGEST'] == '${{ needs.index.outputs.uc-digest }}'
    assert bundle['env']['WEB_DIGEST'] == '${{ needs.index.outputs.web-digest }}'
    readme = tmp_path / '.github' / 'release' / 'airgapped-README.txt'
    readme.parent.mkdir(parents=True)
    readme.write_text('@VERSION@ @ARCH@\n', encoding='utf-8')
    stub(tmp_path, 'git', '#!/bin/sh\necho "$*" >> "$STUB_DIR/git.log"\n')
    stubs = stub(tmp_path, 'docker', DOCKER_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'VERSION': '0.2.1',
        'ARCH': 'arm64',
        'UC_DIGEST': 'sha256:1111',
        'WEB_DIGEST': 'sha256:2222',
        **REPOS,
    }
    script = release_step('bundle', 'Build the air-gapped bundle')['run']
    result = run_bash(tmp_path, script, env, tmp_path)
    assert result.returncode == 0, result.stdout
    assert docker_calls(tmp_path, 'pull') == [
        f'pull {UC}@sha256:1111',
        f'pull {WEB}@sha256:2222',
    ]
    assert docker_calls(tmp_path, 'tag') == [
        f'tag {UC}@sha256:1111 {UC}:0.2.1',
        f'tag {WEB}@sha256:2222 {WEB}:0.2.1',
    ]
    # the tags compose.yaml names, so that docker load gives the images it asks for
    assert docker_calls(tmp_path, 'save') == [
        (
            'save --output bundle/easy-update-center-0.2.1-airgapped-arm64/images.tar '
            f'{UC}:0.2.1 {WEB}:0.2.1'
        )
    ]
    bundle_tar = tmp_path / 'assets' / 'easy-update-center-0.2.1-airgapped-arm64.tar'
    assert bundle_tar.is_file()


def test_the_release_runs_only_on_pushed_release_tags():
    on = load('release.yml')['on']
    assert set(on) == {'push'}
    assert on['push']['tags'] == [
        'v[0-9]+.[0-9]+.[0-9]+',
        'v[0-9]+.[0-9]+.[0-9]+-rc.[0-9]+',
    ]


def test_the_release_checks_a_pushed_tag_on_main(tmp_path):
    result = run_release_check(tmp_path, 'refs/tags/v0.1.0')
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'git') == 'merge-base --is-ancestor abc123 origin/main'
    assert last_call(tmp_path, 'python3') == 'tools/release.py check 0.1.0'
    output = (tmp_path / 'output').read_text(encoding='utf-8')
    assert output == 'version=0.1.0\nprerelease=false\n'


def test_the_release_refuses_a_tag_that_is_not_on_main(tmp_path):
    result = run_release_check(tmp_path, 'refs/tags/v0.1.0', on_main=False)
    assert result.returncode == 1
    assert '::error::v0.1.0 is not on main' in result.stdout
    assert not (tmp_path / 'python3.log').exists()


DOCKER_STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/docker.log"
case "$*" in
    'buildx imagetools inspect'*-uc:*) echo "${STUB_UC_DIGEST:-sha256:1111}" ;;
    'buildx imagetools inspect'*-web:*) echo "${STUB_WEB_DIGEST:-sha256:2222}" ;;
esac
"""
UC = 'ghcr.io/haletechco/easy-update-center-uc'
WEB = 'ghcr.io/haletechco/easy-update-center-web'
REPOS = {
    'UC_REPO': UC,
    'WEB_REPO': WEB,
    'SOURCE_URL': 'https://github.com/HaleTechCo/easy-update-center',
}


def docker_calls(tmp_path: Path, prefix: str) -> list[str]:
    log = (tmp_path / 'docker.log').read_text(encoding='utf-8').splitlines()
    return [line for line in log if line.startswith(prefix)]


GIT_LOG_STUB = (
    '#!/bin/sh\necho "$*" >> "$STUB_DIR/git.log"\necho 2026-10-17T15:00:00-07:00\n'
)
DIGEST_A, DIGEST_B = 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64


def run_image_metadata(
    tmp_path: Path, cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict]:
    stubs = stub(tmp_path, 'git', GIT_LOG_STUB)
    output = tmp_path / 'output'
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'GITHUB_SHA': 'abc123',
        'GITHUB_OUTPUT': str(output),
    }
    script = release_step('check', 'Work out the image metadata')['run']
    result = run_bash(tmp_path, script, env, cwd)
    lines = output.read_text(encoding='utf-8').splitlines() if output.exists() else []
    return result, dict(line.split('=', 1) for line in lines)


def test_the_image_metadata_reads_the_base_of_each_real_dockerfile(tmp_path):
    # A FROM line the step cannot read would stop a release at its first job.
    result, meta = run_image_metadata(tmp_path, Path('/'))
    assert result.returncode == 0, result.stdout
    assert meta['created'] == '2026-10-17T15:00:00-07:00'
    assert (tmp_path / 'git.log').read_text(encoding='utf-8') == (
        'log -1 --format=%cI abc123\n'
    )
    for image in ('uc', 'web'):
        dockerfile = Path(f'/{image}/Dockerfile').read_text(encoding='utf-8')
        name, digest = re.search(
            r'^FROM (\S+)@(sha256:[0-9a-f]{64}) AS root$', dockerfile, re.MULTILINE
        ).groups()
        assert meta[f'{image}-base-name'] == f'docker.io/library/{name}'
        assert meta[f'{image}-base-digest'] == digest


def test_the_image_metadata_names_a_base_outside_docker_hub_as_it_is(tmp_path):
    for image, base in (
        ('uc', f'ghcr.io/acme/base:1@{DIGEST_A}'),
        ('web', f'httpd:2.4@{DIGEST_B}'),
    ):
        (tmp_path / image).mkdir()
        (tmp_path / image / 'Dockerfile').write_text(
            f'FROM {base} AS root\n', encoding='utf-8'
        )
    result, meta = run_image_metadata(tmp_path, tmp_path)
    assert result.returncode == 0, result.stdout
    assert meta['uc-base-name'] == 'ghcr.io/acme/base:1'
    assert meta['uc-base-digest'] == DIGEST_A
    assert meta['web-base-name'] == 'docker.io/library/httpd:2.4'


def test_the_image_metadata_refuses_a_base_not_pinned_by_digest(tmp_path):
    for image, base in (
        ('uc', 'eclipse-temurin:21-jre'),
        ('web', f'httpd:2.4@{DIGEST_B}'),
    ):
        (tmp_path / image).mkdir()
        (tmp_path / image / 'Dockerfile').write_text(
            f'FROM {base} AS root\n', encoding='utf-8'
        )
    result, _ = run_image_metadata(tmp_path, tmp_path)
    assert result.returncode == 1
    assert (
        "::error::uc/Dockerfile has no 'FROM <image>@sha256:<digest> AS root' line"
        in (result.stdout)
    )


def test_the_index_job_tags_nothing_a_user_would_pull(tmp_path):
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
        'CREATED': '2026-10-17T15:00:00-07:00',
        'GITHUB_SHA': 'abc123',
        'GITHUB_OUTPUT': str(output),
        **REPOS,
    }
    script = release_step('index', 'Create the multi-architecture indexes')['run']
    result = run_bash(tmp_path, script, env, tmp_path)
    assert result.returncode == 0, result.stdout
    creates = docker_calls(tmp_path, 'buildx imagetools create')
    assert len(creates) == 2
    for repo, line in zip((UC, WEB), creates, strict=True):
        assert re.findall(r'--tag (\S+)', line) == [f'{repo}:build-abc123']
        assert line.endswith(f'{repo}@sha256:aaaa {repo}@sha256:bbbb')
    # Each index carries its own image's OCI annotations, and the release's.
    for line, title, description in (
        (creates[0], 'uc', 'Generates, signs and publishes the update center'),
        (creates[1], 'web', 'Serves the update center (Apache httpd)'),
    ):
        annotations = re.findall(
            r'--annotation index:org\.opencontainers\.image\.([a-z.]+)=', line
        )
        assert annotations == [
            'title',
            'description',
            'url',
            'documentation',
            'source',
            'vendor',
            'licenses',
            'version',
            'revision',
            'created',
        ]
        assert f'image.title=Easy Update Center for Jenkins: {title} ' in line
        assert f'image.description={description}' in line
        assert 'image.revision=abc123 ' in line
        assert 'image.created=2026-10-17T15:00:00-07:00 ' in line
        assert 'image.licenses=MIT ' in line
    assert output.read_text(encoding='utf-8') == (
        'uc-digest=sha256:1111\nweb-digest=sha256:2222\n'
    )


def test_the_index_job_refuses_a_stray_digest_before_creating_any_index(tmp_path):
    # A digest left by an earlier attempt would give an index two manifests for one
    # platform.
    for image, digests in (('uc', ('aaaa', 'bbbb', 'cccc')), ('web', ('aaaa', 'bbbb'))):
        (tmp_path / 'digests' / image).mkdir(parents=True)
        for digest in digests:
            (tmp_path / 'digests' / image / digest).touch()
    stubs = stub(tmp_path, 'docker', DOCKER_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'VERSION': '0.2.1',
        'GITHUB_SHA': 'abc123',
        'GITHUB_OUTPUT': str(tmp_path / 'output'),
        **REPOS,
    }
    script = release_step('index', 'Create the multi-architecture indexes')['run']
    result = run_bash(tmp_path, script, env, tmp_path)
    assert result.returncode == 1
    assert '::error::expected 2 uc digests, one per platform, found 3' in result.stdout
    assert not (tmp_path / 'docker.log').exists()


def run_tagging(
    tmp_path: Path,
    *,
    prerelease: str,
    version: str = '0.2.1',
    uc_resolves_to: str = 'sha256:1111',
) -> subprocess.CompletedProcess[str]:
    stubs = stub(tmp_path, 'docker', DOCKER_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_UC_DIGEST': uc_resolves_to,
        'VERSION': version,
        'PRERELEASE': prerelease,
        'UC_DIGEST': 'sha256:1111',
        'WEB_DIGEST': 'sha256:2222',
        **REPOS,
    }
    script = release_step('publish', 'Tag the images')['run']
    return run_bash(tmp_path, script, env, tmp_path)


DIGESTS = {UC: 'sha256:1111', WEB: 'sha256:2222'}


def test_publish_tags_a_release_as_x_y_z_x_y_and_latest(tmp_path):
    result = run_tagging(tmp_path, prerelease='false')
    assert result.returncode == 0, result.stdout
    creates = docker_calls(tmp_path, 'buildx imagetools create')
    # each tag for both images before the next tag, so a failure never leaves a moving
    # tag naming a mixed pair; each image's tags name that image's own digest
    assert creates == [
        f'buildx imagetools create --tag {repo}:{tag} {repo}@{DIGESTS[repo]}'
        for tag in ['0.2.1', '0.2', 'latest']
        for repo in (UC, WEB)
    ]


def test_publish_tags_a_candidate_with_its_own_version_only(tmp_path):
    result = run_tagging(tmp_path, prerelease='true', version='0.3.0-rc.1')
    assert result.returncode == 0, result.stdout
    assert docker_calls(tmp_path, 'buildx imagetools create') == [
        f'buildx imagetools create --tag {repo}:0.3.0-rc.1 {repo}@{DIGESTS[repo]}'
        for repo in (UC, WEB)
    ]


def test_publish_stops_when_a_tag_names_another_digest(tmp_path):
    result = run_tagging(tmp_path, prerelease='false', uc_resolves_to='sha256:9999')
    assert result.returncode == 1
    assert (
        f'::error::{UC}:0.2.1 is sha256:9999, not the signed sha256:1111'
        in result.stdout
    )
    assert docker_calls(tmp_path, 'buildx imagetools create') == [
        f'buildx imagetools create --tag {UC}:0.2.1 {UC}@sha256:1111'
    ]


def test_the_release_refuses_an_asset_of_2_gib_naming_it(tmp_path):
    script = release_step('draft', 'Check the asset sizes')['run']
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


RELEASE_GH_STUB = """#!/bin/sh
echo "$*" >> "$STUB_DIR/gh.log"
case "$1 $2" in
    'release view')
        if [ "${STUB_RELEASE_VIEW:-0}" != 0 ]; then
            echo "${STUB_VIEW_ERROR:-release not found}" >&2
            exit "$STUB_RELEASE_VIEW"
        fi
        printf '%s\\n' "${STUB_DRAFT:-true}" ;;
    'api --paginate') cat "$STUB_DIR/release-pages.json" ;;
esac
"""
"""gh for the draft and publish jobs.

release view fails with STUB_VIEW_ERROR when STUB_RELEASE_VIEW is not 0; api prints
release-pages.json, as gh api --paginate prints the pages: one JSON array after another.
"""


def run_draft(
    tmp_path: Path,
    *,
    exists: bool,
    draft: bool = True,
    prerelease: str = 'false',
    view_error: str = 'release not found',
) -> subprocess.CompletedProcess[str]:
    """Run the step that creates or updates the draft Release.

    gh release view fails with view_error when the release does not exist.
    """
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'bundle.tar').touch()
    notes = '#!/bin/sh\necho "$*" >> "$STUB_DIR/python3.log"\necho "- A change."\n'
    stub(tmp_path, 'python3', notes)
    stubs = stub(tmp_path, 'gh', RELEASE_GH_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'STUB_RELEASE_VIEW': '0' if exists else '1',
        'STUB_DRAFT': 'true' if draft else 'false',
        'STUB_VIEW_ERROR': view_error,
        'RUNNER_TEMP': str(tmp_path),
        'VERSION': '0.1.0',
        'PRERELEASE': prerelease,
    }
    script = release_step('draft', 'Create or update the draft Release')['run']
    return run_bash(tmp_path, script, env, tmp_path)


def test_the_first_run_creates_a_draft_release(tmp_path):
    result = run_draft(tmp_path, exists=False)
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'python3') == 'tools/release.py notes 0.1.0'
    assert last_call(tmp_path, 'gh') == (
        f'release create v0.1.0 --draft --verify-tag --title 0.1.0 --notes-file '
        f'{tmp_path}/notes.md assets/bundle.tar'
    )


@pytest.mark.parametrize('prerelease', ['true', ''])
def test_anything_but_false_is_drafted_as_a_prerelease(tmp_path, prerelease):
    # the same rule as tagging and publishing: only 'false' is a release
    result = run_draft(tmp_path, exists=False, prerelease=prerelease)
    assert result.returncode == 0, result.stdout
    assert '--prerelease' in last_call(tmp_path, 'gh')


def test_a_rerun_updates_the_existing_draft(tmp_path):
    result = run_draft(tmp_path, exists=True)
    assert result.returncode == 0, result.stdout
    calls = (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()
    assert calls[1:] == [
        f'release edit v0.1.0 --notes-file {tmp_path}/notes.md',
        'release upload v0.1.0 assets/bundle.tar --clobber',
    ]


def test_a_published_release_is_never_changed(tmp_path):
    result = run_draft(tmp_path, exists=True, draft=False)
    assert result.returncode == 1
    assert '::error::v0.1.0 is already published' in result.stdout


def test_a_failure_to_look_the_release_up_creates_nothing(tmp_path):
    # Only "release not found" means there is none: a second draft for the tag could be
    # the one publish makes permanent.
    result = run_draft(tmp_path, exists=False, view_error='HTTP 502: Bad Gateway')
    assert result.returncode == 1
    assert '::error::cannot tell whether v0.1.0 has a Release' in result.stdout
    assert 'HTTP 502: Bad Gateway' in result.stdout
    calls = (tmp_path / 'gh.log').read_text(encoding='utf-8').splitlines()
    assert [call for call in calls if not call.startswith('release view')] == []


ASSETS = [
    'easy-update-center-0.1.0-airgapped-amd64.tar',
    'easy-update-center-0.1.0-airgapped-arm64.tar',
    'easy-update-center-uc-0.1.0.spdx.json',
    'easy-update-center-web-0.1.0.spdx.json',
    'SHA256SUMS',
    'SHA256SUMS.sigstore.json',
]
"""What the draft job uploads for 0.1.0."""


def release(tag: str, assets: list[str], *, draft: bool = True) -> dict:
    """Return a release as the GitHub API lists it."""
    return {
        'tag_name': tag,
        'draft': draft,
        'assets': [{'name': name} for name in assets],
    }


def run_draft_check(
    tmp_path: Path, pages: list[list[dict]]
) -> subprocess.CompletedProcess[str]:
    """Run publish's check of the draft, with gh listing the releases in pages."""
    (tmp_path / 'release-pages.json').write_text(
        '\n'.join(json.dumps(page) for page in pages), encoding='utf-8'
    )
    stubs = stub(tmp_path, 'gh', RELEASE_GH_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'RUNNER_TEMP': str(tmp_path),
        'GH_REPO': 'HaleTechCo/easy-update-center',
        'VERSION': '0.1.0',
    }
    script = release_step('publish', 'Check the draft Release')['run']
    return run_bash(tmp_path, script, env, tmp_path)


def test_publish_checks_the_draft_before_tagging_anything():
    names = [each.get('name') for each in jobs('release.yml')['publish']['steps']]
    assert names[0] == 'Check the draft Release'
    assert names.index('Tag the images') < names.index('Publish the GitHub Release')


def test_publish_accepts_one_draft_with_the_six_assets(tmp_path):
    pages = [
        [release('v0.0.9', ASSETS[:1], draft=False)],
        [release('v0.1.0', list(reversed(ASSETS)))],
    ]
    result = run_draft_check(tmp_path, pages)
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'gh') == (
        'api --paginate repos/HaleTechCo/easy-update-center/releases?per_page=100'
    )


def test_publish_refuses_two_releases_for_the_tag(tmp_path):
    pages = [[release('v0.1.0', ASSETS)], [release('v0.1.0', ASSETS)]]
    result = run_draft_check(tmp_path, pages)
    assert result.returncode == 1
    assert '::error::v0.1.0 has 2 Releases, not exactly one' in result.stdout


def test_publish_refuses_a_tag_without_a_release(tmp_path):
    result = run_draft_check(tmp_path, [[release('v0.0.9', ASSETS)]])
    assert result.returncode == 1
    assert '::error::v0.1.0 has 0 Releases, not exactly one' in result.stdout


@pytest.mark.parametrize(
    'assets',
    [ASSETS[1:], [*ASSETS, 'extra.txt'], []],
    ids=['missing', 'extra', 'none'],
)
def test_publish_refuses_a_draft_without_exactly_the_six_assets(tmp_path, assets):
    result = run_draft_check(tmp_path, [[release('v0.1.0', assets)]])
    assert result.returncode == 1
    assert '::error::the draft of v0.1.0 has the assets [' in result.stdout


def test_publish_refuses_a_release_that_is_no_longer_a_draft(tmp_path):
    result = run_draft_check(tmp_path, [[release('v0.1.0', ASSETS, draft=False)]])
    assert result.returncode == 1
    assert '::error::v0.1.0 is already published: if this run published it' in (
        result.stdout
    )


@pytest.mark.parametrize(
    ('version', 'prerelease', 'latest'),
    [('0.1.0', 'false', 'true'), ('0.2.0-rc.1', 'true', 'false')],
)
def test_publish_publishes_the_draft(tmp_path, version, prerelease, latest):
    stubs = stub(tmp_path, 'gh', RELEASE_GH_STUB)
    env = {
        'PATH': f'{stubs}:{PATH}',
        'STUB_DIR': str(tmp_path),
        'VERSION': version,
        'PRERELEASE': prerelease,
    }
    script = release_step('publish', 'Publish the GitHub Release')['run']
    result = run_bash(tmp_path, script, env, tmp_path)
    assert result.returncode == 0, result.stdout
    assert last_call(tmp_path, 'gh') == (
        f'release edit v{version} --draft=false --latest={latest}'
    )
