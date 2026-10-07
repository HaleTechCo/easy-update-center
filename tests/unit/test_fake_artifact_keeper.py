"""The test-only fake Artifact Keeper, as the adapter's Artifact Keeper backend sees it."""

import base64
import datetime as dt
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from archivefixture import make_hpi, pom_xml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))

from fake_artifact_keeper import make_handler
from httpclient import put
from uc.repo_adapter.backends import ArtifactKeeperBackend, Http
from uc.repo_adapter.cache import Cache
from uc.repo_adapter.listing import build_index

GAV = ('io.jenkins.plugins', 'caffeine-api', '3.2.4')
BASE = 'io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4'


@pytest.fixture
def keeper(tmp_path):
    repo = tmp_path / 'jenkins-prod-local'
    for number in range(150):  # more than one page of 100
        path = repo / 'org' / 'example' / f'p{number:03d}' / '1.0' / f'p{number:03d}-1.0.pom'
        path.parent.mkdir(parents=True)
        path.write_bytes(pom_xml('org.example', f'p{number:03d}', '1.0'))
    (repo / BASE).parent.mkdir(parents=True)
    (repo / f'{BASE}.hpi').write_bytes(make_hpi(*GAV))
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(tmp_path, 'zac', 'tok', anonymous_read=False))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{httpd.server_address[1]}/'
    httpd.shutdown()
    httpd.server_close()


def backend(url, tmp_path):
    return ArtifactKeeperBackend(Http(None), url, 'jenkins-prod-local', username='zac', token='tok', tmp_dir=tmp_path / 'tmp', max_archive_bytes=1 << 20)


def test_the_backend_pages_through_the_fake(keeper, tmp_path):
    entries = backend(keeper, tmp_path).list()
    assert len(entries) == 151
    assert entries[0].path == f'{BASE}.hpi'


def test_the_fake_serves_side_files_and_refuses_anonymous_reads(keeper, tmp_path):
    entries = {e.path: e for e in backend(keeper, tmp_path).list()}
    assert len(backend(keeper, tmp_path).sha1(entries[f'{BASE}.hpi'])) == 40
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f'{keeper}maven/jenkins-prod-local/{BASE}.hpi')
    err.value.close()  # it holds the response
    assert err.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f'{keeper}api/v1/repositories/jenkins-prod-local/artifacts')
    err.value.close()
    assert err.value.code == 401


def test_the_listing_fills_in_the_pom_from_the_fake(keeper, tmp_path):
    index = build_index(backend(keeper, tmp_path), Cache(tmp_path / 'cache'), key='jenkins-prod-local', now=dt.datetime(2026, 9, 23, tzinfo=dt.UTC))
    assert index.poms[f'{BASE}.pom'] == pom_xml(*GAV)
    assert index.report.derived_poms == [{'gav': ':'.join(GAV), 'url': f'https://repo.jenkins-ci.org/releases/{BASE}.pom'}]
    assert len(index.rows) == 152  # the archive, its derived POM and 150 standalone POMs


def test_the_fake_accepts_an_upload_once(keeper, tmp_path):
    basic = 'Basic ' + base64.b64encode(b'zac:tok').decode()
    url = f'{keeper}maven/jenkins-prod-local/org/example/new/1.0/new-1.0.pom'
    stored = tmp_path / 'jenkins-prod-local' / 'org/example/new/1.0/new-1.0.pom'
    assert put(url, b'<project/>') == 401
    assert put(url, b'<project/>', basic) == 201
    assert stored.read_bytes() == b'<project/>'
    assert put(url, b'<other/>', basic) == 409
    assert stored.read_bytes() == b'<project/>'


def test_the_fake_answers_head_without_a_body(keeper):
    basic = 'Basic ' + base64.b64encode(b'zac:tok').decode()

    def head(path):
        request = urllib.request.Request(f'{keeper}maven/jenkins-prod-local/{path}', method='HEAD', headers={'Authorization': basic})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            exc.close()  # it holds the response
            return exc.code, b''

    assert head(f'{BASE}.hpi') == (200, b'')
    assert head('org/example/absent/1.0/absent-1.0.pom') == (404, b'')
