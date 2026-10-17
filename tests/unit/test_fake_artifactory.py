"""The test-only fake speaks the same three calls update-center2 makes.

As seen by our own clients.
"""

import base64
import hashlib
import io
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))

from fake_artifactory import aql_results, make_handler
from httpclient import put
from uc.aql import Gav, classify, query_repository
from uc.doctor import ArtifactSource


def hpi(short_name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(
            'META-INF/MANIFEST.MF',
            f'Manifest-Version: 1.0\r\nShort-Name: {short_name}\r\n\r\n',
        )
    return buffer.getvalue()


@pytest.fixture
def server(tmp_path):
    base = tmp_path / 'jenkins' / 'org' / 'jenkins-ci' / 'plugins' / 'git' / '5.2.0'
    base.mkdir(parents=True)
    (base / 'git-5.2.0.hpi').write_bytes(hpi('git'))
    (base / 'git-5.2.0.pom').write_text('<project><name>Git</name></project>')
    (tmp_path / 'jenkins' / 'README.txt').write_text('ignored')

    def start(anonymous_read):
        httpd = ThreadingHTTPServer(
            ('127.0.0.1', 0),
            make_handler(tmp_path, 'uc:pw', anonymous_read=anonymous_read),
        )
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f'http://127.0.0.1:{httpd.server_address[1]}/artifactory/'

    servers = []
    yield tmp_path, start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def test_aql_results_match_the_generator_schema(server):
    root, _ = server
    results = aql_results(root, 'jenkins')
    assert [r['name'] for r in results] == ['git-5.2.0.hpi', 'git-5.2.0.pom']
    assert set(results[0]) == {
        'repo',
        'path',
        'name',
        'modified',
        'created',
        'sha256',
        'actual_sha1',
        'size',
    }
    assert results[0]['path'] == 'org/jenkins-ci/plugins/git/5.2.0'
    assert results[0]['modified'].endswith('.000Z')


def test_our_aql_client_reads_the_fake(server):
    _, start = server
    url = start(anonymous_read=False)
    listing = classify(query_repository(url + 'api/', 'jenkins', 'uc', 'pw'))
    assert [g.artifact_id for g in listing.plugins] == ['git']


def test_archive_entries_and_files(server):
    _, start = server
    source = ArtifactSource(
        start(anonymous_read=False),
        'jenkins',
        'uc',
        'pw',
        cache_dir=None,
        allow_network=True,
    )
    gav = Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi')
    assert 'Short-Name: git' in source.manifest(gav)
    assert source.pom(gav) == b'<project><name>Git</name></project>'


def test_file_reads_honour_range(server):
    _, start = server
    url = (
        start(anonymous_read=True)
        + 'jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'
    )
    request = urllib.request.Request(url, headers={'Range': 'bytes=1-7'})
    with urllib.request.urlopen(request) as response:
        assert response.status == 206
        assert response.headers['Content-Range'] == 'bytes 1-7/35'
        assert response.read() == b'project'


def test_private_reads_challenge_for_basic_auth(server):
    _, start = server
    url = (
        start(anonymous_read=False)
        + 'jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(url)
    err.value.close()  # it holds the response
    assert err.value.code == 401
    assert err.value.headers['WWW-Authenticate'] == 'Basic realm="Artifactory Realm"'


@pytest.fixture
def writable(tmp_path):
    httpd = ThreadingHTTPServer(
        ('127.0.0.1', 0), make_handler(tmp_path, 'uc:secret', anonymous_read=False)
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}/artifactory/'
    httpd.shutdown()
    httpd.server_close()


def test_the_fake_accepts_an_upload_once_and_serves_its_sha1(writable, tmp_path):
    basic = 'Basic ' + base64.b64encode(b'uc:secret').decode()
    url = writable + 'jenkins/org/example/new/1.0/new-1.0.pom'
    assert put(url, b'<project/>') == 401
    assert put(url, b'<project/>', basic) == 201
    assert put(url, b'<other/>', basic) == 409
    assert (
        tmp_path / 'jenkins/org/example/new/1.0/new-1.0.pom'
    ).read_bytes() == b'<project/>'
    with urllib.request.urlopen(
        urllib.request.Request(url + '.sha1', headers={'Authorization': basic})
    ) as response:
        assert (
            response.read().decode()
            == hashlib.sha1(b'<project/>', usedforsecurity=False).hexdigest()
        )


def test_client_checksums_are_served_only_when_the_upload_sent_one(tmp_path):
    httpd = ThreadingHTTPServer(
        ('127.0.0.1', 0),
        make_handler(
            tmp_path, 'uc:secret', anonymous_read=True, checksum_policy='client'
        ),
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = (
        f'http://127.0.0.1:{httpd.server_address[1]}/artifactory/jenkins/org/example/'
    )
    basic = 'Basic ' + base64.b64encode(b'uc:secret').decode()
    digest = hashlib.sha1(b'<project/>', usedforsecurity=False).hexdigest()

    def put_with(url, data, sha1):
        request = urllib.request.Request(
            url,
            data=data,
            method='PUT',
            headers={'Authorization': basic, 'X-Checksum-Sha1': sha1},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            exc.close()  # it holds the response
            return exc.code

    def status(url, method='GET'):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, method=method)
            ) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            exc.close()
            return exc.code, b''

    try:
        assert put(base + 'plain/1.0/plain-1.0.pom', b'<project/>', basic) == 201
        assert status(base + 'plain/1.0/plain-1.0.pom.sha1') == (404, b'')
        assert status(base + 'plain/1.0/plain-1.0.pom', 'HEAD')[0] == 200
        assert put_with(base + 'bad/1.0/bad-1.0.pom', b'<project/>', '0' * 40) == 409
        assert not (tmp_path / 'jenkins/org/example/bad/1.0/bad-1.0.pom').exists()
        assert put_with(base + 'good/1.0/good-1.0.pom', b'<project/>', digest) == 201
        assert status(base + 'good/1.0/good-1.0.pom.sha1') == (200, digest.encode())
        assert status(base + 'none/1.0/none-1.0.pom', 'HEAD')[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
