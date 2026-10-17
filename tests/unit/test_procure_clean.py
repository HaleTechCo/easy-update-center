"""Unit tests for uc.procure.clean.

Deleting the staged files the repository already holds.
"""

import hashlib
import io
import socket

import pytest

from httpstub import StubServer
from procurefixture import SOURCE, publish, stage
from uc.procure import ProcureError
from uc.procure.clean import clean
from uc.procure.staging import MANIFEST, Staging
from ucsupport import make_settings

POM = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.pom'
HPI = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.hpi'
WAR = 'org/jenkins-ci/main/jenkins-war/2.479.1/jenkins-war-2.479.1.war'


def keeper_settings(tmp_path, secrets_dir, url):
    (secrets_dir / 'repo_push_auth').write_text('uc:tok\n')
    return make_settings(
        tmp_path,
        secrets_dir,
        REPO_TYPE='artifact-keeper',
        REPO_URL=url,
        REPO_KEY='jenkins',
        REPO_ADAPTER='on',
    )


def staged(stage_dir, files):
    staging = Staging.open(stage_dir, SOURCE)
    for path, data in files.items():
        stage(staging, path, data)
    staging.save()


def listed(stage_dir):
    return set(Staging.open(stage_dir, SOURCE).files)


def run(settings, stage_dir, *, dry_run=False):
    out = io.StringIO()
    return clean(
        settings, stage_dir, dry_run=dry_run, out=out
    ), out.getvalue().splitlines()


def line(label, path, detail=''):
    return f'{label:<12} {path}' + (f'  ({detail})' if detail else '')


def test_files_the_repository_holds_are_removed_and_the_rest_kept(
    keeper, tmp_path, secrets_dir
):
    repo, url = keeper
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>', HPI: b'hpi bytes'})
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert not (tmp_path / 'stage' / POM).exists()
    assert (tmp_path / 'stage' / HPI).read_bytes() == b'hpi bytes'
    assert listed(tmp_path / 'stage') == {HPI}
    assert lines == [
        line('kept', HPI, 'not in the repository yet'),
        line('removed', POM),
        f'1 removed, 1 kept, 0 problem(s) -> {url}maven/jenkins/',
    ]
    assert not [line for line in lines if line.startswith('next:')]


def test_files_placed_by_hand_are_checked_too(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    publish(repo, WAR, b'war bytes')
    publish(tmp_path / 'stage', WAR, b'war bytes')
    publish(tmp_path / 'stage', HPI, b'hpi bytes')
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert not (tmp_path / 'stage' / WAR).exists()
    assert (tmp_path / 'stage' / HPI).exists()
    assert not (
        tmp_path / 'stage' / MANIFEST
    ).exists()  # nothing was listed, so there is no record to write
    assert line('removed', WAR) in lines
    assert line('kept', HPI, 'not in the repository yet') in lines


def test_emptied_folders_go_and_the_staging_folder_stays(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    publish(repo, POM, b'<project/>')
    publish(repo, WAR, b'war bytes')
    # placed by hand: no procurement.json, so nothing but the staging folder's own
    # existence can keep it
    publish(tmp_path / 'stage', POM, b'<project/>')
    publish(tmp_path / 'stage', WAR, b'war bytes')
    code, _ = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert (tmp_path / 'stage').is_dir()
    assert list((tmp_path / 'stage').iterdir()) == []


def test_a_dry_run_removes_nothing(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    code, lines = run(
        keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage', dry_run=True
    )
    assert code == 0
    assert (tmp_path / 'stage' / POM).is_file()
    assert listed(tmp_path / 'stage') == {POM}
    assert lines == [
        line('would remove', POM),
        f'0 removed, 1 would remove, 0 kept, 0 problem(s) -> {url}maven/jenkins/',
    ]


def test_a_different_file_in_the_repository_keeps_the_staged_one(
    keeper, tmp_path, secrets_dir
):
    repo, url = keeper
    publish(repo, POM, b'<html>404</html>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 1
    assert (tmp_path / 'stage' / POM).is_file()
    assert listed(tmp_path / 'stage') == {POM}
    digest = hashlib.sha1(b'<html>404</html>', usedforsecurity=False).hexdigest()
    assert lines == [
        line('DIFFERENT', POM, f'the repository holds another file (sha1 {digest})'),
        f'0 removed, 0 kept, 1 problem(s) -> {url}maven/jenkins/',
    ]


def test_the_bytes_in_the_folder_decide_not_the_record(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    (tmp_path / 'stage' / POM).write_bytes(b'<edited/>')
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 1
    assert (tmp_path / 'stage' / POM).read_bytes() == b'<edited/>'
    assert [entry for entry in lines if entry.startswith(line('DIFFERENT', POM))]


def test_a_listed_file_already_gone_is_forgotten_once_the_repository_holds_it(
    keeper, tmp_path, secrets_dir
):
    repo, url = keeper
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>', HPI: b'hpi bytes'})
    (tmp_path / 'stage' / POM).unlink()
    (tmp_path / 'stage' / HPI).unlink()
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert listed(tmp_path / 'stage') == {HPI}
    assert line('removed', POM, 'already gone from the folder') in lines
    assert (
        line(
            'kept',
            HPI,
            'missing from the folder and not in the repository; download it again',
        )
        in lines
    )


def test_an_unreachable_repository_keeps_every_file(tmp_path, secrets_dir):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    code, lines = run(
        keeper_settings(tmp_path, secrets_dir, f'http://127.0.0.1:{port}/'),
        tmp_path / 'stage',
    )
    assert code == 1
    assert (tmp_path / 'stage' / POM).is_file()
    assert lines[0].startswith(
        line(
            'ERROR',
            POM,
            f'GET http://127.0.0.1:{port}/maven/jenkins/{POM}.sha1 failed: ',
        )[:-1]
    )


def test_a_path_the_repository_holds_without_a_checksum_is_kept(
    client_checksums, tmp_path, secrets_dir
):
    repo, url = client_checksums
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    code, lines = run(
        make_settings(tmp_path, secrets_dir, REPO_URL=url, REPO_KEY='jenkins'),
        tmp_path / 'stage',
    )
    assert code == 1
    assert (tmp_path / 'stage' / POM).is_file()
    assert (
        line(
            'ERROR',
            POM,
            (
                'the repository holds this path but publishes no checksum for it; '
                'check it by hand'
            ),
        )
        in lines
    )


def test_artifactory_files_are_compared_under_its_repository_key(
    artifactory, tmp_path, secrets_dir
):
    repo, url = artifactory
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    code, lines = run(
        make_settings(tmp_path, secrets_dir, REPO_URL=url, REPO_KEY='jenkins'),
        tmp_path / 'stage',
    )
    assert code == 0
    assert line('removed', POM) in lines


def test_a_file_that_cannot_be_deleted_stays_listed(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    folder = (tmp_path / 'stage' / POM).parent
    folder.chmod(0o555)  # the unit tests run as uid 10001, so this really is read-only
    try:
        code, lines = run(
            keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage'
        )
    finally:
        folder.chmod(0o755)
    assert code == 1
    assert (tmp_path / 'stage' / POM).is_file()
    assert listed(tmp_path / 'stage') == {POM}
    assert lines[0].startswith(
        line('ERROR', POM, 'cannot delete it: [Errno 13] Permission denied')[:-1]
    )


def test_other_files_are_left_alone(keeper, tmp_path, secrets_dir):
    _, url = keeper
    (tmp_path / 'stage').mkdir()
    (tmp_path / 'stage' / 'notes.txt').write_text('reviewed')
    (tmp_path / 'stage' / '.part-abc123').write_bytes(b'')
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert lines == [f'0 removed, 0 kept, 0 problem(s) -> {url}maven/jenkins/']
    assert sorted(path.name for path in (tmp_path / 'stage').iterdir()) == [
        '.part-abc123',
        'notes.txt',
    ]


def test_the_public_repository_is_never_compared(tmp_path, secrets_dir):
    settings = make_settings(
        tmp_path,
        secrets_dir,
        MODE='online',
        REPO_URL='https://repo.jenkins-ci.org/',
        REPO_KEY='releases',
    )
    with pytest.raises(ProcureError, match=r'public repo\.jenkins-ci\.org'):
        clean(settings, tmp_path, dry_run=True)


def test_bytes_artifact_keeper_stored_without_recording_them_are_kept_for_push(
    tmp_path, secrets_dir
):
    routes = {
        ('GET', f'/maven/jenkins/{POM}.sha1'): (404, {}, b''),
        ('HEAD', f'/maven/jenkins/{POM}'): (200, {}, b''),
    }
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    with StubServer(routes) as stub:
        code, lines = run(
            keeper_settings(tmp_path, secrets_dir, stub.url + '/'), tmp_path / 'stage'
        )
    assert code == 0
    assert (tmp_path / 'stage' / POM).is_file()
    assert lines == [
        line(
            'kept',
            POM,
            (
                'Artifact Keeper stored it without recording it; '
                'push checks it and records it'
            ),
        ),
        f'0 removed, 1 kept, 0 problem(s) -> {stub.url}/maven/jenkins/',
    ]
    assert [request[0] for request in stub.requests] == ['GET', 'HEAD']
