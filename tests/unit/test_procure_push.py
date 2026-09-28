"""Unit tests for uc.procure.push: uploading exactly the staged files, never overwriting."""

import hashlib
import io
import socket
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


@pytest.fixture
def client_checksums(tmp_path):
    """Serve a fake Artifactory with the client checksum policy, the default for Artifactory's local repositories."""
    httpd, url = serve(artifactory_handler(tmp_path / 'repo', 'uc:tok', anonymous_read=False, checksum_policy='client'))
    yield tmp_path / 'repo' / 'jenkins', url + 'artifactory/'
    httpd.shutdown()
    httpd.server_close()


class RecordingHttp(Http):
    """An Http that records the method and URL of every request."""

    def __init__(self):
        """Verify TLS against the system bundle; nothing recorded yet."""
        super().__init__(None)
        self.calls = []

    def request(self, url, **kwargs):
        """Record the request, then make it."""
        self.calls.append((kwargs.get('method') or 'GET', url))
        return super().request(url, **kwargs)


def sha1(data):
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def keeper_settings(tmp_path, secrets_dir, url, auth='uc:tok'):
    (secrets_dir / 'repo_push_auth').write_text(auth + '\n')
    return make_settings(tmp_path, secrets_dir, REPO_TYPE='artifact-keeper', REPO_URL=url, REPO_KEY='jenkins', REPO_ADAPTER='on')


def staged(stage_dir, files, **fields):
    staging = Staging.open(stage_dir, SOURCE)
    for path, data in files.items():
        stage(staging, path, data, **fields)
    staging.save()


def run(settings, stage_dir, *, dry_run=False, http=None):
    out = io.StringIO()
    return push(settings, stage_dir, dry_run=dry_run, out=out, http=http), out.getvalue().splitlines()


def artifactory_settings(tmp_path, secrets_dir, url):
    (secrets_dir / 'repo_push_auth').write_text('uc:tok')
    return make_settings(tmp_path, secrets_dir, REPO_URL=url, REPO_KEY='jenkins')


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
    assert f"DIFFERENT {POM}  (the repository holds another file (sha1 {digest}); delete it in the repository first, then run Artifact Keeper's storage garbage collection)" in lines


def test_on_artifactory_a_different_file_only_needs_deleting(artifactory, tmp_path, secrets_dir):
    repo, url = artifactory
    publish(repo, POM, b'<html>404</html>')
    staged(tmp_path / 'stage', {POM: b'<project/>'}, replaces_unusable=True)
    code, lines = run(artifactory_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 1
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
    assert f'0 uploaded, 1 would put, 0 already present, 0 problem(s) -> {url}maven/jenkins/' in lines
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


def test_artifactory_with_client_checksums_records_the_sha1_sent_with_the_upload(client_checksums, tmp_path, secrets_dir):
    repo, url = client_checksums
    staged(tmp_path / 'stage', {POM: b'<project/>', HPI: b'hpi bytes'})
    code, lines = run(artifactory_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0, lines
    assert (repo / POM).read_bytes() == b'<project/>'
    assert (repo / f'{POM}.sha1').read_text() == sha1(b'<project/>')
    assert f'uploaded  {HPI}' in lines
    assert f'uploaded  {POM}' in lines


@pytest.mark.parametrize('dry_run', [False, True])
def test_a_path_the_repository_holds_without_a_checksum_is_never_uploaded(client_checksums, tmp_path, secrets_dir, dry_run):
    repo, url = client_checksums
    publish(repo, POM, b'<other/>')
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    http = RecordingHttp()
    code, lines = run(artifactory_settings(tmp_path, secrets_dir, url), tmp_path / 'stage', dry_run=dry_run, http=http)
    assert code == 1
    assert f'ERROR     {POM}  (the repository holds this path but publishes no checksum for it; check it by hand)' in lines
    assert (repo / POM).read_bytes() == b'<other/>'
    assert [method for method, _ in http.calls] == ['GET', 'HEAD']


def test_an_unreachable_repository_is_an_error_and_nothing_is_uploaded(tmp_path, secrets_dir):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    http = RecordingHttp()
    code, lines = run(keeper_settings(tmp_path, secrets_dir, f'http://127.0.0.1:{port}/'), tmp_path / 'stage', http=http)
    assert code == 1
    assert [line for line in lines if line.startswith(f'ERROR     {POM}  (GET http://127.0.0.1:{port}/maven/jenkins/{POM}.sha1 failed: ')]
    assert [method for method, _ in http.calls] == ['GET']


def test_an_upload_the_repository_then_reports_with_another_sha1_failed(tmp_path, secrets_dir):
    data = b'<project/>'
    answers = iter([(404, {}, b''), (200, {}, b'0' * 40)])
    routes = {('GET', f'/maven/jenkins/{POM}.sha1'): lambda _headers, _body: next(answers), ('HEAD', f'/maven/jenkins/{POM}'): (404, {}, b''), ('PUT', f'/maven/jenkins/{POM}'): (201, {}, b'')}
    staged(tmp_path / 'stage', {POM: data})
    with StubServer(routes) as stub:
        code, lines = run(keeper_settings(tmp_path, secrets_dir, stub.url + '/'), tmp_path / 'stage')
    assert code == 1
    assert f'FAILED    {POM}  (HTTP 201, repository sha1 {"0" * 40})' in lines
    [(_, _, headers, body)] = [request for request in stub.requests if request[0] == 'PUT']
    assert body == data
    assert {key.lower(): value for key, value in headers.items()}['x-checksum-sha1'] == sha1(data)


def test_an_unexpected_answer_to_the_existence_check_is_an_error(tmp_path, secrets_dir):
    routes = {('GET', f'/maven/jenkins/{POM}.sha1'): (404, {}, b''), ('HEAD', f'/maven/jenkins/{POM}'): (403, {}, b'')}
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    with StubServer(routes) as stub:
        code, lines = run(keeper_settings(tmp_path, secrets_dir, stub.url + '/'), tmp_path / 'stage')
    assert code == 1
    assert f'ERROR     {POM}  (the repository answered HTTP 403)' in lines
    assert [request[0] for request in stub.requests] == ['GET', 'HEAD']


def test_an_unreadable_staged_file_is_reported_and_the_push_goes_on(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>', HPI: b'hpi bytes'})
    (tmp_path / 'stage' / HPI).chmod(0o000)  # the unit tests run as uid 10001, so this really is unreadable
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 1
    assert [line for line in lines if line.startswith(f'TAMPERED  {HPI}  (cannot read it: [Errno 13] Permission denied')]
    assert not (repo / HPI).exists()
    assert (repo / POM).read_bytes() == b'<project/>'
    assert f'1 uploaded, 0 already present, 1 problem(s) -> {url}maven/jenkins/' in lines


TOMBSTONE = 'Artifact Keeper keeps a deleted release as a tombstone until its storage garbage collection runs'


@pytest.mark.parametrize(
    ('artifact_keeper', 'reply', 'detail'),
    [
        (True, (409, {'Content-Type': 'application/json'}, b'{"code":"CONFLICT","message":"Artifact version already exists and is immutable"}'), f'HTTP 409: Artifact version already exists and is immutable, repository sha1 none; {TOMBSTONE}'),
        (False, (409, {'Content-Type': 'application/json'}, b'{"errors":[{"status":409,"message":"Checksum policy rejects the upload"}]}'), 'HTTP 409: Checksum policy rejects the upload, repository sha1 none'),
        (True, (500, {'Content-Type': 'text/html'}, b'<html><body>Internal Server Error</body></html>'), 'HTTP 500, repository sha1 none'),
        (False, (400, {'Content-Type': 'text/plain'}, b'  Bad\n request:   path   rejected  '), 'HTTP 400: Bad request: path rejected, repository sha1 none'),
    ],
)
def test_a_refused_upload_reports_the_repositorys_reason(tmp_path, secrets_dir, artifact_keeper, reply, detail):
    prefix = '/maven/jenkins/' if artifact_keeper else '/artifactory/jenkins/'
    routes = {('GET', f'{prefix}{POM}.sha1'): (404, {}, b''), ('HEAD', f'{prefix}{POM}'): (404, {}, b''), ('PUT', f'{prefix}{POM}'): reply}
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    with StubServer(routes) as stub:
        settings = keeper_settings(tmp_path, secrets_dir, stub.url + '/') if artifact_keeper else artifactory_settings(tmp_path, secrets_dir, stub.url + '/artifactory/')
        code, lines = run(settings, tmp_path / 'stage')
    assert code == 1
    assert f'FAILED    {POM}  ({detail})' in lines


def test_files_procurement_json_does_not_list_are_reported_not_pushed(keeper, tmp_path, secrets_dir):
    repo, url = keeper
    staged(tmp_path / 'stage', {POM: b'<project/>'})
    publish(tmp_path / 'stage', HPI, b'hpi bytes')  # copied in by hand
    (tmp_path / 'stage' / 'notes.txt').write_text('reviewed')
    (tmp_path / 'stage' / '.part-abc123').write_bytes(b'')  # a download interrupted mid-way
    code, lines = run(keeper_settings(tmp_path, secrets_dir, url), tmp_path / 'stage')
    assert code == 0
    assert not (repo / HPI).exists()
    assert f'unlisted  {HPI}  (not in procurement.json, so not pushed; record it with uc doctor --stage-local)' in lines
    assert 'unlisted  notes.txt  (not in procurement.json, so not pushed; not a Maven path)' in lines
    assert not [line for line in lines if '.part-' in line]
    assert f'1 uploaded, 0 already present, 0 problem(s), 2 not in procurement.json -> {url}maven/jenkins/' in lines
