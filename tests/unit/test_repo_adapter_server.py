"""Unit tests for uc.repo_adapter.server.

The adapter's HTTP endpoints, the credentials they need and its token.
"""

import base64
import datetime as dt
import re
import socket
import stat
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from archivefixture import make_hpi, make_jar, pom_xml
from fakebackend import FakeBackend
from uc.aql import AQL_TEMPLATE, Gav, classify, query_repository
from uc.doctor import ArtifactSource
from uc.errors import UcError
from uc.repo_adapter import server as server_module
from uc.repo_adapter.cache import Cache
from uc.repo_adapter.listing import read_report
from uc.repo_adapter.server import (
    REQUEST_TIMEOUT,
    Adapter,
    AdapterServer,
    RecordPortError,
    UnhealthyError,
    ensure_token,
    healthcheck,
    make_handler,
    make_server,
    record_port,
)
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
        adapter = (
            Adapter(
                backend,
                Cache(tmp_path / 'cache'),
                key=KEY,
                report_file=tmp_path / 'report.json',
                clock=lambda: NOW,
            )
            if adapter_on
            else None
        )
        server = ThreadingHTTPServer(
            ('127.0.0.1', 0), make_handler(adapter, key=KEY, token=TOKEN)
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return backend, f'http://127.0.0.1:{server.server_address[1]}/artifactory/'

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def fetch(url, *, token=TOKEN, data=None):
    headers = (
        {'Authorization': 'Basic ' + base64.b64encode(f'uc:{token}'.encode()).decode()}
        if token
        else {}
    )
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, dict(exc.headers), exc.read()
        finally:
            exc.close()  # it holds the response


def aql(url, key=KEY):
    return fetch(url + 'api/search/aql', data=(AQL_TEMPLATE % key).encode())


def test_health_needs_no_credentials(running):
    _, url = running({})
    assert fetch(url.replace('/artifactory/', '/healthz'), token=None)[0] == 200


def test_the_listing_needs_the_token(running):
    _, url = running({})
    status, headers, _ = fetch(
        url + 'api/search/aql', token=None, data=(AQL_TEMPLATE % KEY).encode()
    )
    assert status == 401
    assert headers['WWW-Authenticate'] == 'Basic realm="repo-adapter"'
    assert fetch(url + 'api/search/aql', token='wrong', data=b'x')[0] == 401


def test_file_reads_need_no_credentials(running):
    """Upstream sends none, and cannot answer a challenge.

    Its Authenticator NPEs on Java's HttpClient.
    """
    _, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    aql(url)
    assert fetch(f'{url}{KEY}/{BASE}.pom', token=None)[2] == pom_xml(*GAV)
    assert (
        b'Group-Id'
        in fetch(f'{url}{KEY}/{BASE}.hpi!/META-INF/MANIFEST.MF', token=None)[2]
    )


def test_only_upstreams_query_for_the_configured_repository_is_answered(running):
    _, url = running({})
    assert fetch(url + 'api/search/aql', data=b'items.find({"repo":"other"})')[0] == 400
    assert aql(url, key='other')[0] == 400


def test_upstream_and_uc_read_the_gap_filled_listing(running, tmp_path):
    _, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    listing = classify(
        query_repository(url + 'api/', KEY, 'uc', TOKEN)
    )  # uc's own AQL client
    assert [g.artifact_id for g in listing.plugins] == [
        'caffeine-api'
    ]  # paired with its derived POM
    assert read_report(tmp_path / 'report.json')['derived_poms'] == [
        {
            'gav': ':'.join(GAV),
            'url': f'https://repo.jenkins-ci.org/releases/{BASE}.pom',
        }
    ]


def test_derived_poms_manifests_and_descriptions_are_served(running):
    _, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    aql(url)
    source = ArtifactSource(
        url, KEY, 'uc', TOKEN, cache_dir=None, allow_network=True
    )  # what uc doctor reads with
    gav = Gav(*GAV, 'hpi')
    assert source.pom(gav) == pom_xml(*GAV)
    assert 'Group-Id: io.jenkins.plugins' in source.manifest(gav)
    status, _, body = fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')
    assert (status, body) == (200, b'<div>An excellent plugin.</div>')


def test_a_repository_jar_wins_for_the_description(running):
    _, url = running(
        {
            f'{BASE}.hpi': make_hpi(*GAV),
            f'{BASE}.jar': make_jar(b'<div>From the jar.</div>'),
        }
    )
    aql(url)
    assert (
        fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')[2] == b'<div>From the jar.</div>'
    )


def test_artifactory_descriptions_try_the_repository_jar_first(running):
    backend, url = running({f'{BASE}.hpi': make_hpi(*GAV)}, backend_type='artifactory')
    aql(url)
    assert (
        fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')[2]
        == b'<div>An excellent plugin.</div>'
    )
    assert ('jar', f'{BASE}.jar') in backend.calls


def test_repository_poms_are_forwarded_and_archives_are_not_served(running):
    own = pom_xml(*GAV, name='Own')
    _backend, url = running({f'{BASE}.hpi': make_hpi(*GAV), f'{BASE}.pom': own})
    aql(url)
    assert fetch(f'{url}{KEY}/{BASE}.pom')[2] == own
    assert (
        fetch(f'{url}{KEY}/{BASE}.hpi')[0] == 404
    )  # upstream never downloads whole archives
    assert (
        fetch(f'{url}{KEY}/{BASE}.hpi!/WEB-INF/lib/caffeine-api.jar')[0] == 404
    )  # not an entry upstream reads
    assert (
        fetch(f'{url}{KEY}/org/jenkins-ci/plugins/plugin/4.88/plugin-4.88.pom')[0]
        == 404
    )


def test_a_get_before_any_listing_builds_one(running):
    backend, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    assert fetch(f'{url}{KEY}/{BASE}.pom')[2] == pom_xml(
        *GAV
    )  # as after an adapter restart
    assert backend.calls[0] == ('list', '')


def test_a_listing_that_fails_unexpectedly_answers_502(running):
    backend, url = running({})

    def explode():
        msg = 'boom'
        raise RuntimeError(msg)

    backend.list = explode
    assert aql(url)[0] == 502


def test_descriptions_are_only_fetched_for_listed_plugins(running):
    """Forwarding carries the service account.

    So it must not reach paths the listing does not hold.
    """
    backend, url = running({f'{BASE}.hpi': make_hpi(*GAV)}, backend_type='artifactory')
    aql(url)
    assert fetch(f'{url}{KEY}/../other-repo/org/x/a/1/a-1.jar!/index.jelly')[0] == 404
    assert fetch(f'{url}{KEY}/org/x/unlisted/1/unlisted-1.jar!/index.jelly')[0] == 404
    assert [call for call in backend.calls if call[0] == 'jar'] == []
    assert (
        fetch(f'{url}{KEY}/{BASE}.jar!/index.jelly')[0] == 200
    )  # a listed plugin still works


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
    idle = make_server(
        make_settings(tmp_path, secrets_dir, MODE='online', REPO_URL='', REPO_KEY=''),
        port=0,
        host='127.0.0.1',
    )
    idle.server_close()
    settings = make_settings(tmp_path, secrets_dir, REPO_ADAPTER='on')
    server = make_server(settings, port=0, host='127.0.0.1')
    server.server_close()
    assert settings.repo_adapter_token_file.is_file()


def test_healthcheck_reports_a_listener_that_does_not_speak_http():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]

        def answer():
            connection, _ = listener.accept()
            with connection:
                connection.recv(4096)
                connection.sendall(b'hello\r\n')

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        with pytest.raises(
            UnhealthyError, match=f'^repo-adapter is not healthy on port {port}: '
        ):
            healthcheck(port)
        thread.join(timeout=10)


def test_record_port_into_a_missing_directory_is_a_uc_error(tmp_path):
    path = tmp_path / 'missing' / 'adapter.port'
    with pytest.raises(UcError) as caught:
        record_port(4321, path)
    assert str(caught.value).startswith(
        f'repo-adapter cannot record its port in {path}: '
    )
    assert not path.with_name('adapter.port.tmp').exists()


def test_a_failed_replace_is_reported_and_removes_the_temporary_port_file(
    tmp_path, monkeypatch
):
    path = tmp_path / 'adapter.port'
    # the temporary file is written beside it, then cannot replace a directory
    path.mkdir()
    replace = Path.replace
    written = []

    def watched_replace(self, target):
        written.append((self.name, self.read_text(encoding='utf-8')))
        return replace(self, target)

    monkeypatch.setattr(Path, 'replace', watched_replace)
    with pytest.raises(
        RecordPortError,
        match=(
            r'^repo-adapter cannot record its port in '
            rf'{re.escape(str(path))}: Is a directory$'
        ),
    ):
        record_port(4321, path)
    assert written == [('adapter.port.tmp', '4321\n')]
    assert [entry.name for entry in tmp_path.iterdir()] == ['adapter.port']
    assert path.is_dir()


def raw(port, request, timeout=10):
    """Send request bytes to the server on port.

    Return the status line it answers with.
    """
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as sock:
        sock.sendall(request)
        return sock.makefile('rb').readline().decode().strip()


def served(port, request, deadline=5.0):
    """Return the first status line that is not 503.

    Retrying while the server is busy for up to deadline seconds (a handler thread may
    still be finishing the previous request).
    """
    end = time.monotonic() + deadline
    while True:
        status = raw(port, request)
        if not status.startswith('HTTP/1.1 503') or time.monotonic() > end:
            return status
        time.sleep(0.05)


@pytest.fixture
def capped():
    started = []

    def start(max_requests, adapter=None):
        server = AdapterServer(
            ('127.0.0.1', 0),
            make_handler(adapter, key=KEY, token=TOKEN),
            max_requests=max_requests,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server.server_address[1]

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('length', ['-1', 'many'])
def test_a_listing_with_a_bad_content_length_is_refused(running, length):
    _, url = running({})
    port = int(url.split(':')[2].split('/')[0])
    auth = base64.b64encode(f'uc:{TOKEN}'.encode()).decode()
    assert (
        raw(
            port,
            (
                'POST /artifactory/api/search/aql HTTP/1.1\r\nHost: x\r\n'
                f'Authorization: Basic {auth}\r\nContent-Length: {length}\r\n\r\n'
            ).encode(),
        )
        == 'HTTP/1.0 400 Bad Request'
    )


def test_requests_beyond_the_cap_get_503_at_once(capped):
    port = capped(1)
    with socket.create_connection(
        ('127.0.0.1', port)
    ) as idle:  # holds the only slot: it sends no end of headers
        idle.sendall(b'GET /healthz HTTP/1.1\r\n')
        assert (
            served(port, b'GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n', deadline=0)
            == 'HTTP/1.1 503 Service Unavailable'
        )


def test_a_slot_comes_back_after_failed_requests(capped):
    port = capped(1)
    for _ in range(3):  # a leaked slot would leave every later request at 503
        assert (
            served(
                port, b'POST /nowhere HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n'
            )
            == 'HTTP/1.0 404 Not Found'
        )
        assert served(port, b'BREW / HTTP/1.1\r\nHost: x\r\n\r\n').startswith(
            'HTTP/1.0 501'
        )
    assert (
        served(port, b'GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n') == 'HTTP/1.0 200 OK'
    )


def test_connections_time_out():
    assert REQUEST_TIMEOUT == 120
    assert make_handler(None, key=KEY, token=TOKEN).timeout == REQUEST_TIMEOUT


def test_make_server_caps_the_requests(tmp_path, secrets_dir):
    server = make_server(make_settings(tmp_path, secrets_dir), port=0, host='127.0.0.1')
    try:
        assert isinstance(server, AdapterServer)
    finally:
        server.server_close()


def port_of(url):
    return int(url.split(':')[2].split('/')[0])


def test_a_non_ascii_authorization_gets_401_not_a_crash(running):
    _backend, url = running({})
    body = (AQL_TEMPLATE % KEY).encode()
    request = (
        b'POST /artifactory/api/search/aql HTTP/1.0\r\n'
        b'Authorization: Basic \xe9\xff\r\n'
        b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body
    )
    with socket.create_connection(('127.0.0.1', port_of(url)), timeout=5) as conn:
        conn.sendall(request)
        reply = conn.recv(1024)
    assert reply.startswith(b'HTTP/1.0 401')


def test_a_request_cannot_forge_a_log_line(running, capfd):
    _backend, url = running({})
    with socket.create_connection(('127.0.0.1', port_of(url)), timeout=5) as conn:
        conn.sendall(
            f'GET /artifactory/{KEY}/x.hpi!/%0Aforged HTTP/1.0\r\n\r\n'.encode()
        )
        conn.recv(1024)
    err = capfd.readouterr().err
    assert '\nforged' not in err
    assert '\\nforged' in err or '\\x0aforged' in err


def test_a_connection_closed_before_the_answer_ends_quietly(capfd):
    # A client that hangs up, or one the read deadline cut off, leaves the answer
    # nowhere to go: a line in the log, not socketserver's traceback.
    handler = make_handler(None, key=KEY, token=TOKEN)
    ours, theirs = socket.socketpair()
    theirs.sendall(b'GET /healthz HTTP/1.0\r\n')  # and no more: the client hangs up
    theirs.close()
    with ours:
        handler(ours, ('127.0.0.1', 1), SimpleNamespace())  # raises nothing
    assert 'connection closed before the answer was sent' in capfd.readouterr().err


def test_a_listing_slower_than_the_read_deadline_still_finishes(running, monkeypatch):
    # The deadline is for reading the request; a listing of a large repository takes
    # minutes, and the connection must stay open for its answer.
    monkeypatch.setattr(server_module, 'READ_DEADLINE', 0.2)
    refresh = Adapter.refresh

    def slow_refresh(self):
        time.sleep(1)
        return refresh(self)

    monkeypatch.setattr(Adapter, 'refresh', slow_refresh)
    _backend, url = running({f'{BASE}.hpi': make_hpi(*GAV)})
    status, _headers, body = aql(url)
    assert status == 200
    assert b'caffeine-api-3.2.4.hpi' in body


def test_a_client_that_never_finishes_its_request_is_cut_off(running, monkeypatch):
    monkeypatch.setattr(server_module, 'READ_DEADLINE', 0.5)
    _backend, url = running({})
    with socket.create_connection(('127.0.0.1', port_of(url)), timeout=5) as conn:
        conn.sendall(b'GET /healthz HTTP/1.0\r\n')  # and never the blank line
        started = time.monotonic()
        assert conn.recv(1024) == b''
    assert time.monotonic() - started < 4
