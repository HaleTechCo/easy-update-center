"""uc repo-adapter: upstream's three Artifactory requests.

Answered from either repository type
(docs/architecture/repo-adapter.md#requests-it-answers).
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import http.client
import json
import os
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

from uc.aql import AQL_TEMPLATE, RepoFile, to_gav
from uc.config import Settings, read_secret
from uc.errors import UcError
from uc.http import Http
from uc.preflight import ADAPTER_USERNAME, load_repository_credentials
from uc.timeutil import utcnow

from .archive import MANIFEST, SMALL_ENTRY_LIMIT
from .backends import ArtifactKeeperBackend, ArtifactoryBackend, Backend
from .cache import Cache
from .listing import Index, build_index, write_report

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable

PREFIX = '/artifactory/'
QUERY_LIMIT = 64 * 1024
PORT = 8081
HEALTH_PATH = '/healthz'
# the container's root is read-only; /tmp is its private tmpfs (compose.yaml)
PORT_FILE = Path('/tmp/uc-repo-adapter.port')  # noqa: S108
"""Where the running adapter records the port it bound, for --healthcheck.

Health follows the server, not the settings files.
"""
HEALTH_TIMEOUT = 3
"""Seconds uc repo-adapter --healthcheck waits for the adapter to answer."""
HTTP_OK = 200
MAX_REQUESTS = 32
"""Requests handled at once; more get 503 straight away instead of a thread each."""
REQUEST_TIMEOUT = 120
"""Seconds a connection may wait for the client before it is closed."""
BUSY = (
    b'HTTP/1.1 503 Service Unavailable\r\n'
    b'Content-Length: 0\r\nConnection: close\r\n\r\n'
)


class AdapterServer(ThreadingHTTPServer):
    """A threading HTTP server that handles at most max_requests requests at once.

    It answers the rest 503.
    """

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        max_requests: int = MAX_REQUESTS,
    ) -> None:
        """Bind to address and serve with handler, max_requests at a time."""
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(max_requests)

    def process_request(self, request, client_address) -> None:
        """Start a thread for the request when a slot is free.

        Otherwise answer 503 and close it.
        """
        if not self.slots.acquire(blocking=False):
            with contextlib.suppress(OSError):
                request.sendall(BUSY)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        """Handle the request, then give its slot back, whatever happened."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class RecordPortError(UcError):
    """The adapter could not record its port, so --healthcheck could never pass."""


class UnhealthyError(UcError):
    """The repo-adapter did not answer its health check."""


def ensure_token(path: Path) -> str:
    """Return the generator's credential for the adapter.

    32 random bytes, created once, mode 0600.
    """
    existing = read_secret(path)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        out.write(token + '\n')
    tmp.replace(path)
    return token


class Adapter:
    """Upstream's requests, answered from the latest listing of one repository."""

    def __init__(
        self,
        backend: Backend,
        cache: Cache,
        *,
        key: str,
        report_file: Path,
        clock: Callable[[], dt.datetime] = utcnow,
    ) -> None:
        """Serve repository key from backend.

        Each listing's report goes to report_file.
        """
        self.backend, self.cache, self.key, self.report_file, self.clock = (
            backend,
            cache,
            key,
            report_file,
            clock,
        )
        self.index: Index | None = None
        self.lock = threading.Lock()

    def refresh(self) -> Index:
        """Build a new listing, write its report and serve GETs from it.

        One at a time.
        """
        with self.lock:
            index = build_index(
                self.backend, self.cache, key=self.key, now=self.clock()
            )
            if index.report is None:
                msg = 'build_index returned a listing without a report'
                raise RuntimeError(msg)
            write_report(index.report, self.report_file)
            self.index = index
            return index

    def current(self) -> Index:
        """Return the latest listing; after a restart, the first GET builds one."""
        return self.index or self.refresh()

    def pom(self, path: str) -> bytes | None:
        """Return a listed POM.

        The one derived from its archive, else the repository's; None when not listed.
        """
        index = self.current()
        if path not in index.poms:
            return None
        derived = index.poms[path]
        return (
            derived
            if derived is not None
            else self.backend.get(path, SMALL_ENTRY_LIMIT)
        )

    def manifest(self, archive_path: str) -> bytes | None:
        """Return a listed archive's MANIFEST.MF, None when the listing has none."""
        return self.current().manifests.get(archive_path)

    def description(self, jar_path: str) -> bytes | None:
        """<artifactId>-<version>.jar!/index.jelly.

        From the repository's .jar, else from the .hpi (rule 6).
        """
        index = self.current()
        directory, _, name = jar_path.rpartition('/')
        gav = to_gav(RepoFile(directory, name))
        if gav is None or gav.extension != 'jar':
            return None
        coordinates = (gav.group_id, gav.artifact_id, gav.version)
        # never forward a path the listing does not hold, ".." included
        if coordinates not in index.plugins:
            return None
        jar = index.jars.get(jar_path)
        if self.backend.type == 'artifactory':
            found = self.backend.jar_description(jar_path)
            if found is not None:
                return found
        elif jar is not None:
            if not self.cache.has_description(jar.sha256):
                self.cache.store_description(
                    jar.sha256, self.backend.jar_description(jar_path)
                )
            found = self.cache.description(jar.sha256)
            if found is not None:
                return found
        return index.descriptions.get(coordinates)


def make_handler(adapter: Adapter | None, *, key: str, token: str | None):
    """Return the request handler class.

    With adapter None, it answers the repository requests with 503.
    """
    expected = (
        ('Basic ' + base64.b64encode(f'{ADAPTER_USERNAME}:{token}'.encode()).decode())
        if token
        else ''
    )
    repo_prefix = f'{PREFIX}{key}/'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'uc-repo-adapter'
        timeout = REQUEST_TIMEOUT

        # http.server's signature
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            sys.stderr.write(f'repo-adapter: {self.address_string()} {format % args}\n')

        def _send(
            self,
            status: int,
            body: bytes = b'',
            content_type: str = 'application/octet-stream',
        ) -> None:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def _refuse(self, *, authenticate: bool) -> bool:
            """Return True when the request was answered already.

            Idle adapter, or no valid credentials.

            Only the listing is authenticated. Upstream cannot answer an authentication
            challenge for file reads: its Authenticator dereferences the requesting
            site, which Java's HttpClient always passes as null. GETs serve metadata
            only, on the internal repo network
            (docs/architecture/repo-adapter.md#security).
            """
            if adapter is None:
                self._send(503, b'REPO_ADAPTER is off\n', 'text/plain')
                return True
            if authenticate and not hmac.compare_digest(
                self.headers.get('Authorization', ''), expected
            ):
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm="repo-adapter"')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return True
            return False

        def do_GET(self) -> None:
            path = self.path.split('?', 1)[0]
            if path == HEALTH_PATH:
                return self._send(200, b'ok\n', 'text/plain')
            if self._refuse(authenticate=False):
                return None
            if adapter is None:
                msg = '_refuse answers every request while the adapter is idle'
                raise RuntimeError(msg)
            if not path.startswith(repo_prefix):
                return self._send(404)
            archive, separator, entry = urllib.parse.unquote(
                path[len(repo_prefix) :]
            ).partition('!/')
            try:
                if not separator:
                    data = adapter.pom(archive) if archive.endswith('.pom') else None
                elif entry == MANIFEST and archive.endswith(('.hpi', '.jpi', '.war')):
                    data = adapter.manifest(archive)
                elif entry == 'index.jelly' and archive.endswith('.jar'):
                    data = adapter.description(archive)
                else:
                    self.log_message('refused archive entry %s!/%s', archive, entry)
                    data = None
            # any failure is the repository's, reported as a 502
            except Exception as exc:  # noqa: BLE001
                self.log_message('GET %s failed: %r', path, exc)
                return self._send(502, b'repository error\n', 'text/plain')
            return self._send(200, data) if data is not None else self._send(404)

        do_HEAD = do_GET  # noqa: N815 - http.server dispatches on the do_<METHOD> name

        def do_POST(self) -> None:
            if self.path.split('?', 1)[0] != PREFIX + 'api/search/aql':
                return self._send(404)
            if self._refuse(authenticate=True):
                return None
            if adapter is None:
                msg = '_refuse answers every request while the adapter is idle'
                raise RuntimeError(msg)
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = -1
            if length < 0:
                return self._send(400, b'bad Content-Length\n', 'text/plain')
            if length > QUERY_LIMIT:
                return self._send(413)
            query = self.rfile.read(length).decode('utf-8', 'replace')
            if query != AQL_TEMPLATE % key:
                return self._send(400, b'unsupported query\n', 'text/plain')
            try:
                rows = adapter.refresh().rows
            # any failure is the repository's, reported as a 502
            except Exception as exc:  # noqa: BLE001
                self.log_message('listing failed: %r', exc)
                return self._send(502, b'repository error\n', 'text/plain')
            body = {
                'results': rows,
                'range': {'start_pos': 0, 'end_pos': len(rows), 'total': len(rows)},
            }
            return self._send(200, json.dumps(body).encode(), 'application/json')

    return Handler


def create_adapter(settings: Settings) -> Adapter:
    """Build the adapter for the configured repository type and credentials."""
    username, password = load_repository_credentials(settings)
    http = Http(settings.repo_ca_file)
    max_bytes = settings.repo_max_archive_mb * 1024 * 1024
    backend: Backend
    if settings.repo_type == 'artifact-keeper':
        backend = ArtifactKeeperBackend(
            http,
            settings.repo_url,
            settings.repo_key,
            username=username,
            token=password,
            tmp_dir=settings.repo_adapter_dir / 'tmp',
            max_archive_bytes=max_bytes,
        )
    else:
        backend = ArtifactoryBackend(
            http,
            settings.repo_url,
            settings.repo_api_url,
            settings.repo_key,
            username=username,
            password=password,
            max_archive_bytes=max_bytes,
        )
    return Adapter(
        backend,
        Cache(settings.repo_adapter_dir / 'cache'),
        key=settings.repo_key,
        report_file=settings.repo_adapter_report_file,
    )


def make_server(
    settings: Settings,
    port: int = PORT,
    # the repo-adapter listens on every interface of its container by design
    host: str = '0.0.0.0',  # noqa: S104
) -> AdapterServer:
    """Bind the repo-adapter's HTTP server.

    It stays idle, answering 503, while REPO_ADAPTER is off.
    """
    if not settings.repo_adapter:
        return AdapterServer(
            (host, port), make_handler(None, key=settings.repo_key, token=None)
        )
    adapter = create_adapter(settings)
    token = ensure_token(settings.repo_adapter_token_file)
    return AdapterServer(
        (host, port), make_handler(adapter, key=settings.repo_key, token=token)
    )


def record_port(port: int, path: Path | None = None) -> None:
    """Record the port the server is listening on, where --healthcheck finds it.

    Atomically.
    """
    path = PORT_FILE if path is None else path
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_text(f'{port}\n', encoding='utf-8')
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        msg = f'repo-adapter cannot record its port in {path}: {exc.strerror or exc}'
        raise RecordPortError(msg) from None


def recorded_port(path: Path | None = None) -> int:
    """Return the port record_port saved.

    Raise UnhealthyError when the adapter has not recorded one.
    """
    path = PORT_FILE if path is None else path
    try:
        return int(path.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        msg = 'repo-adapter is not healthy: it has not recorded a port yet'
        raise UnhealthyError(msg) from None


def healthcheck(port: int | None = None) -> None:
    """Raise UnhealthyError unless the repo-adapter on this host answers 200.

    Within HEALTH_TIMEOUT seconds. It probes port, else the port the running adapter
    recorded; it reads no settings, never starts a server and ignores the proxy
    settings.
    """
    port = recorded_port() if port is None else port
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            f'http://127.0.0.1:{port}{HEALTH_PATH}', timeout=HEALTH_TIMEOUT
        ) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()  # it holds the response
    # HTTPException: something on the port answers with bytes that are not HTTP
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        reason = (
            exc.reason if isinstance(exc, urllib.error.URLError) else exc
        ) or type(exc).__name__
        msg = f'repo-adapter is not healthy on port {port}: {reason}'
        raise UnhealthyError(msg) from None
    if status != HTTP_OK:
        msg = f'repo-adapter is not healthy on port {port}: HTTP {status}'
        raise UnhealthyError(msg)


def serve(settings: Settings, port: int = PORT) -> None:
    """Run the repo-adapter on port until the process stops."""
    server = make_server(settings, port)
    try:
        record_port(server.server_address[1])
    except RecordPortError:
        server.server_close()
        raise
    state = (
        f'{settings.repo_type} repository {settings.repo_key} at {settings.repo_url}'
        if settings.repo_adapter
        else 'idle (REPO_ADAPTER is off)'
    )
    print(f'repo-adapter: listening on :{port}, {state}', flush=True)
    server.serve_forever()
