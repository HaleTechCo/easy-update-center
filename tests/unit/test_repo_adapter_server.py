"""Unit tests for uc.repo_adapter.server: the adapter's HTTP endpoints, the credentials they need and its token."""

import base64
import datetime as dt
import stat
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from archivefixture import make_hpi, make_jar, pom_xml
from fakebackend import FakeBackend
from uc.aql import AQL_TEMPLATE, Gav, classify, query_repository
from uc.doctor import ArtifactSource
from uc.repo_adapter.cache import Cache
from uc.repo_adapter.listing import read_report
from uc.repo_adapter.server import Adapter, ensure_token, make_handler, make_server
from ucsupport import make_settings

NOW = dt.datetime(2026, 9, 23, tzinfo=dt.UTC)
KEY = 'jenkins-prod-local'
TOKEN = 't0ken'
GAV = ('io.jenkins.plugins', 'caffeine-api', '3.2.4')
BASE = 'io/jenkins/plugins/caffeine-api/3.2.4/caffeine-api-3.2.4'


@pytest.fixture
def running(tmp_path):
    started = []

    def start(files, *, backend_type='artifact-keeper', adapter_on=True):
        backend = FakeBackend(files, backend_type=backend_type)
        adapter = Adapter(backend, Cache(tmp_path / 'cache'), key=KEY, report_file=tmp_path / 'report.json', clock=lambda: NOW) if adapter_on else None
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(adapter, key=KEY, token=TOKEN))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return backend, f'http://127.0.0.1:{server.server_address[1]}/artifactory/'

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def fetch(url, *, token=TOKEN, data=None):
    headers = {'Authorization': 'Basic ' + base64.b64encode(f'uc:{token}'.encode()).decode()} if token else {}
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def aql(url, key=KEY):
    return fetch(url + 'api/search/aql', data=(AQL_TEMPLATE % key).encode())


def test_health_needs_no_credentials(running):
    _, url = running({})
    assert fetch(url.replace('/artifactory/', '/healthz'), token=None)[0] == 200


def test_the_listing_needs_the_token(running):
    _, url = running({})
    status, headers, _ = fetch(url + 'api/search/aql', token=None, data=(AQL_TEMPLATE % KEY).encode())
    assert status == 401
    assert headers['WWW-Authenticate'] == 'Basic realm="repo-adapter"'
    assert fetch(url + 'api/search/aql', token='wrong', data=b'x')[0] == 401


def test_file_reads_need_no_credentials(running):
    """Upstream sends none, and cannot answer a challenge (its Authenticator NPEs on Java's HttpClient)."""
    _, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    aql(url)
    assert fetch(f'{url}{KEY}/{BASE}.pom', token=None)[2] == pom_xml(*GAV)
    assert b'Group-Id' in fetch(f'{url}{KEY}/{BASE}.hpi!/META-INF/MANIFEST.MF', token=None)[2]


def test_only_upstreams_query_for_the_configured_repository_is_answered(running):
    _, url = running({})
    assert fetch(url + 'api/search/aql', data=b'items.find({"repo":"other"})')[0] == 400
    assert aql(url, key='other')[0] == 400


def test_upstream_and_uc_read_the_gap_filled_listing(running, tmp_path):
    _, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    listing = classify(query_repository(url + 'api/', KEY, 'uc', TOKEN))  # uc's own AQL client
    assert [g.artifact_id for g in listing.plugins] == ['caffeine-api']  # paired with its derived POM
    assert read_report(tmp_path / 'report.json')['derived_poms'] == [{'gav': ':'.join(GAV), 'url': f'https://repo.jenkins-ci.org/releases/{BASE}.pom'}]


def test_derived_poms_manifests_and_descriptions_are_served(running):
    _, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    aql(url)
    source = ArtifactSource(url, KEY, 'uc', TOKEN, cache_dir=None, allow_network=True)  # what uc doctor reads with
    gav = Gav(*GAV, 'hpi')
    assert source.pom(gav) == pom_xml(*GAV)
    assert 'Group-Id: io.jenkins.plugins' in source.manifest(gav)
    status, _, body = fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')
    assert (status, body) == (200, b'<div>An excellent plugin.</div>')


def test_a_repository_jar_wins_for_the_description(running):
    _, url = running({f'{BASE}.hpi': make_hpi(*GAV), f'{BASE}.jar': make_jar(b'<div>From the jar.</div>')})
    aql(url)
    assert fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')[2] == b'<div>From the jar.</div>'


def test_artifactory_descriptions_try_the_repository_jar_first(running):
    backend, url = running({f'{BASE}.hpi': make_hpi(*GAV)}, backend_type='artifactory')
    aql(url)
    assert fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')[2] == b'<div>An excellent plugin.</div>'
    assert ('jar', f'{BASE}.jar') in backend.calls


def test_repository_poms_are_forwarded_and_archives_are_not_served(running):
    own = pom_xml(*GAV, name='Own')
    _backend, url = running({f'{BASE}.hpi': make_hpi(*GAV), f'{BASE}.pom': own})
    aql(url)
    assert fetch(f'{url}{KEY}/{BASE}.pom')[2] == own
    assert fetch(f'{url}{KEY}/{BASE}.hpi')[0] == 404  # upstream never downloads whole archives
    assert fetch(f'{url}{KEY}/{BASE}.hpi!/WEB-INF/lib/caffeine-api.jar')[0] == 404  # not an entry upstream reads
    assert fetch(f'{url}{KEY}/org/jenkins-ci/plugins/plugin/4.88/plugin-4.88.pom')[0] == 404


def test_a_get_before_any_listing_builds_one(running):
    backend, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    assert fetch(f'{url}{KEY}/{BASE}.pom')[2] == pom_xml(*GAV)  # as after an adapter restart
    assert backend.calls[0] == ('list', '')


def test_a_listing_that_fails_unexpectedly_answers_502(running):
    backend, url = running({})

    def explode():
        msg = 'boom'
        raise RuntimeError(msg)

    backend.list = explode
    assert aql(url)[0] == 502


def test_descriptions_are_only_fetched_for_listed_plugins(running):
    """Forwarding carries the service account, so it must not reach paths the listing does not hold."""
    backend, url = running({f'{BASE}.hpi': make_hpi(*GAV)}, backend_type='artifactory')
    aql(url)
    assert fetch(f'{url}{KEY}/../other-repo/org/x/a/1/a-1.jar!/index.jelly')[0] == 404
    assert fetch(f'{url}{KEY}/org/x/unlisted/1/unlisted-1.jar!/index.jelly')[0] == 404
    assert [call for call in backend.calls if call[0] == 'jar'] == []
    assert fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')[0] == 200  # a listed plugin still works


def test_an_idle_adapter_only_answers_health(running):
    _, url = running({}, adapter_on=False)
    assert fetch(url.replace('/artifactory/', '/healthz'), token=None)[0] == 200
    assert aql(url)[0] == 503


def test_ensure_token_creates_a_private_token_once(tmp_path):
    path = tmp_path / 'state' / 'token'
    token = ensure_token(path)
    assert len(token) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert ensure_token(path) == token


def test_make_server_needs_repository_credentials_only_when_on(tmp_path, secrets_dir):
    idle = make_server(make_settings(tmp_path, secrets_dir, MODE='online', REPO_URL='', REPO_KEY=''), port=0, host='127.0.0.1')
    idle.server_close()
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    server = make_server(settings, port=0, host='127.0.0.1')
    server.server_close()
    assert settings.repo_adapter_token_file.is_file()
