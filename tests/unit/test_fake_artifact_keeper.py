"""The test-only fake Artifact Keeper, as the adapter's Artifact Keeper backend sees it."""

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
    assert err.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f'{keeper}api/v1/repositories/jenkins-prod-local/artifacts')
    assert err.value.code == 401


def test_the_listing_fills_in_the_pom_from_the_fake(keeper, tmp_path):
    index = build_index(backend(keeper, tmp_path), Cache(tmp_path / 'cache'), key='jenkins-prod-local', now=dt.datetime(2026, 9, 23, tzinfo=dt.UTC))
    assert index.poms[f'{BASE}.pom'] == pom_xml(*GAV)
    assert index.report.derived_poms == [':'.join(GAV)]
    assert len(index.rows) == 152  # the archive, its derived POM and 150 standalone POMs
