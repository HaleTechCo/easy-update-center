"""The test-only fake speaks the same three calls update-center2 makes, as seen by our own clients."""

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
from uc.aql import Gav, classify, query_repository
from uc.doctor import ArtifactSource


def hpi(short_name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF', f'Manifest-Version: 1.0\r\nShort-Name: {short_name}\r\n\r\n')
    return buffer.getvalue()


@pytest.fixture
def server(tmp_path):
    base = tmp_path / 'jenkins' / 'org' / 'jenkins-ci' / 'plugins' / 'git' / '5.2.0'
    base.mkdir(parents=True)
    (base / 'git-5.2.0.hpi').write_bytes(hpi('git'))
    (base / 'git-5.2.0.pom').write_text('<project><name>Git</name></project>')
    (tmp_path / 'jenkins' / 'README.txt').write_text('ignored')

    def start(anonymous_read):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(tmp_path, 'uc:pw', anonymous_read=anonymous_read))
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
    assert set(results[0]) == {'repo', 'path', 'name', 'modified', 'created', 'sha256', 'actual_sha1', 'size'}
    assert results[0]['path'] == 'org/jenkins-ci/plugins/git/5.2.0'
    assert results[0]['modified'].endswith('.000Z')


def test_our_aql_client_reads_the_fake(server):
    _, start = server
    url = start(anonymous_read=False)
    listing = classify(query_repository(url + 'api/', 'jenkins', 'uc', 'pw'))
    assert [g.artifact_id for g in listing.plugins] == ['git']


def test_archive_entries_and_files(server):
    _, start = server
    source = ArtifactSource(start(anonymous_read=False), 'jenkins', 'uc', 'pw', cache_dir=None, allow_network=True)
    gav = Gav('org.jenkins-ci.plugins', 'git', '5.2.0', 'hpi')
    assert 'Short-Name: git' in source.manifest(gav)
    assert source.pom(gav) == b'<project><name>Git</name></project>'


def test_file_reads_honour_range(server):
    _, start = server
    url = start(anonymous_read=True) + 'jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'
    request = urllib.request.Request(url, headers={'Range': 'bytes=1-7'})
    with urllib.request.urlopen(request) as response:
        assert response.status == 206
        assert response.headers['Content-Range'] == 'bytes 1-7/35'
        assert response.read() == b'project'


def test_private_reads_challenge_for_basic_auth(server):
    _, start = server
    url = start(anonymous_read=False) + 'jenkins/org/jenkins-ci/plugins/git/5.2.0/git-5.2.0.pom'
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(url)
    assert err.value.code == 401
    assert err.value.headers['WWW-Authenticate'] == 'Basic realm="Artifactory Realm"'
