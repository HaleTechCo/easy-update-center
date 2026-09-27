"""Unit tests for uc.procure.push: uploading exactly the staged files, never overwriting."""

import hashlib
import io
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from httpstub import StubServer
from procurefixture import SOURCE, publish, stage
from uc.procure import ProcureError
from uc.procure.push import push
from uc.procure.staging import Staging
from uc.repo_adapter.backends import Http
from ucsupport import make_settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))

from fake_artifact_keeper import make_handler as keeper_handler
from fake_artifactory import make_handler as artifactory_handler

POM = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.pom'
HPI = 'org/jenkins-ci/plugins/mailer/534.v1/mailer-534.v1.hpi'


def serve(handler):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f'http://127.0.0.1:{httpd.server_address[1]}/'


@pytest.fixture
def keeper(tmp_path):
    httpd, url = serve(keeper_handler(tmp_path / 'repo', 'uc', 'tok', anonymous_read=False))
    yield tmp_path / 'repo' / 'jenkins', url
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def artifactory(tmp_path):
    httpd, url = serve(artifactory_handler(tmp_path / 'repo', 'uc:tok', anonymous_read=False))
    yield tmp_path / 'repo' / 'jenkins', url + 'artifactory/'
    httpd.shutdown()
    httpd.server_close()


def keeper_settings(tmp_path, secrets_dir, url, auth='uc:tok'):
    (secrets_dir / 'repo_push_auth').write_text(auth + '\n')
    return make_settings(tmp_path, secrets_dir, REPO_TYPE='artifact-keeper', REPO_URL=url, REPO_KEY='jenkins', REPO_ADAPTER='on')


def staged(stage_dir, files, **fields):
    staging = Staging.open(stage_dir, SOURCE)
    for path, data in files.items():
        stage(staging, path, data, **fields)
    staging.save()


def run(settings, stage_dir, *, dry_run=False):
    out = io.StringIO()
    return push(settings, stage_dir, dry_run=dry_run, out=out), out.getvalue().splitlines()


def test_push_uploads_what_the_repository_lacks_and_verifies_it(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>', HPI: b'hpi bytes'})
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert (repo / POM).read_bytes() == b'<project/>'
    assert (repo / HPI).read_bytes() == b'hpi bytes'
    assert f'uploaded  {HPI}' in lines
    assert f'uploaded  {POM}' in lines
    assert f'2 uploaded, 0 already present, 0 problem(s) -> {url}maven/jenkins/' in lines


def test_a_file_already_there_with_the_same_content_is_present(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    publish(repo, POM, b'<project/>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert f'present   {POM}' in lines


def test_a_different_file_is_left_alone(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    publish(repo, POM, b'<html>404</html>')
    staged(tmp_path / 'stage', {POM: b'<project/>'}, replaces_unusable=True)
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 1
    assert (repo / POM).read_bytes() == b'<html>404</html>'
    digest = hashlib.sha1(b'<html>404</html>', usedforsecurity=False).hexdigest()
    assert f'DIFFERENT {POM}  (the repository holds another file (sha1 {digest}); delete it in the repository first)' in lines


def test_a_file_changed_after_download_is_refused(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    (tmp_path / 'stage' / POM).write_bytes(b'<evil/>')
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 1
    assert not (repo / POM).exists()
    assert f'TAMPERED  {POM}  (changed or missing since it was downloaded; download it again)' in lines


def test_an_authentication_failure_is_an_error_not_an_absence(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url, auth='uc:wrong'), tmp_path / 'stage')
    assert code == 1
    assert not (repo / POM).exists()
    assert f'ERROR     {POM}  (the repository answered HTTP 401)' in lines


def test_a_dry_run_uploads_nothing(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage', dry_run=True)
    assert code == 0
    assert f'would put {POM}' in lines
    assert not (repo / POM).exists()


def test_artifactory_is_pushed_under_its_repository_key(artifactory, tmp_path, secrets_dir):
    repo, url = artifactory
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    code, _ = run(make_settings(tmp_path, secrets_dir, REPO_URL=url, REPO_KEY='jenkins'), tmp_path / 'stage')
    assert code == 0
    assert (repo / POM).read_bytes() == b'<project/>'


@pytest.mark.parametrize('secret', ['', 'no-colon', ':token', 'user:'])
def test_the_push_account_must_be_user_and_token(keeper, tmp_path, secrets_dir, secret):
    _, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    with pytest.raises(ProcureError, match='must hold user:token'):
        push(keeper_settings(tmp_path, secrets_dir, url, auth=secret), tmp_path / 'stage', dry_run=True)


def test_the_public_repository_is_never_a_push_target(tmp_path, secrets_dir):
    settings = make_settings(tmp_path, secrets_dir, MODE='online', REPO_URL='https://repo.jenkins-ci.org/', REPO_KEY='releases')
    with pytest.raises(ProcureError, match=r'public repo\.jenkins-ci\.org'):
        push(settings, tmp_path, dry_run=True)


def test_nothing_staged_is_an_error(keeper, tmp_path, secrets_dir):
    _, url = keeper
    with pytest.raises(ProcureError, match='nothing to push'):
        push(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'empty', dry_run=False)


def test_a_conflict_with_the_same_content_is_present(tmp_path, secrets_dir):
    data = b'<project/>'
    answers = iter([(404, {}, b''), (200, {}, hashlib.sha1(data, usedforsecurity=False).hexdigest().encode())])
    routes = {('GET', f'/maven/jenkins/{POM}.sha1'): lambda _headers, _body: next(answers), ('PUT', f'/maven/jenkins/{POM}'): (409, {}, b'{"code":"CONFLICT"}')}
    staged(tmp_path / 'stage', {POM: data})
    with StubServer(routes) as stub:
        code, lines = run(keeper_settings(tmp_path, secrets_dir, stub.url + '/'), tmp_path / 'stage')
    assert code == 0
    assert f'present   {POM}  (the same file arrived while pushing)' in lines


class TamperingHttp:
    """Wraps a real Http, corrupting a file on disk right after the first request goes out."""

    def __init__(self, real, file, data):
        """Forward every request to real; before the very first one, replace file's content with data."""
        self.real, self.file, self.data, self.calls = real, file, data, 0

    def request(self, *args, **kwargs):
        """Corrupt file before the first request, then forward to the wrapped Http."""
        self.calls += 1
        if self.calls == 1:
            self.file.write_bytes(self.data)
        return self.real.request(*args, **kwargs)


def test_push_uploads_the_bytes_it_verified_even_if_the_file_changes_after(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    file = tmp_path / 'stage' / POM
    http = TamperingHttp(Http(None), file, b'<evil/>')
    out = io.StringIO()
    code = push(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage', dry_run=False, out=out, http=http)
    assert code == 0
    assert (repo / POM).read_bytes() == b'<project/>'
    assert f'uploaded  {POM}' in out.getvalue().splitlines()
