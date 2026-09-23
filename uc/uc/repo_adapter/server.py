"""uc repo-adapter: upstream's three Artifactory requests, answered from either repository type
(repo-adapter spec §3, §4.1, §5.3)."""
from __future__ import annotations

import base64
import datetime as dt
import hmac
import json
import os
import secrets
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from ..aql import AQL_TEMPLATE, RepoFile, to_gav
from ..config import Settings, read_secret
from ..preflight import ADAPTER_USERNAME, load_repository_credentials
from ..timeutil import utcnow
from .archive import MANIFEST, ArchiveError
from .backends import ArtifactKeeperBackend, ArtifactoryBackend, Backend, BackendError, Http
from .cache import Cache
from .listing import Index, build_index, write_report

PREFIX = "/artifactory/"
QUERY_LIMIT = 64 * 1024
PORT = 8081


def ensure_token(path: Path) -> str:
    """The generator's credential for the adapter: 32 random bytes, created once, mode 0600."""
    existing = read_secret(path)
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    os.replace(tmp, path)
    return token


class Adapter:
    def __init__(self, backend: Backend, cache: Cache, *, key: str, report_file: Path,
                 clock: Callable[[], dt.datetime] = utcnow) -> None:
        self.backend, self.cache, self.key, self.report_file, self.clock = backend, cache, key, report_file, clock
        self.index: Index | None = None
        self.lock = threading.Lock()

    def refresh(self) -> Index:
        with self.lock:
            index = build_index(self.backend, self.cache, key=self.key, now=self.clock())
            assert index.report is not None
            write_report(index.report, self.report_file)
            self.index = index
            return index

    def current(self) -> Index:
        """The latest listing; after a restart, the first GET builds one (spec §4.1)."""
        return self.index or self.refresh()

    def pom(self, path: str) -> bytes | None:
        index = self.current()
        if path not in index.poms:
            return None
        derived = index.poms[path]
        return derived if derived is not None else self.backend.get(path)

    def manifest(self, archive_path: str) -> bytes | None:
        return self.current().manifests.get(archive_path)

    def description(self, jar_path: str) -> bytes | None:
        """<artifactId>-<version>.jar!/index.jelly: from the repository's .jar, else from the .hpi (rule 6)."""
        index = self.current()
        directory, _, name = jar_path.rpartition("/")
        gav = to_gav(RepoFile(directory, name))
        if gav is None or gav.extension != "jar":
            return None
        jar = index.jars.get(jar_path)
        if self.backend.type == "artifactory":
            found = self.backend.jar_description(jar_path)
            if found is not None:
                return found
        elif jar is not None:
            if not self.cache.has_description(jar.sha256):
                self.cache.store_description(jar.sha256, self.backend.jar_description(jar_path))
            found = self.cache.description(jar.sha256)
            if found is not None:
                return found
        return index.descriptions.get((gav.group_id, gav.artifact_id, gav.version))


def make_handler(adapter: Adapter | None, *, key: str, token: str | None):
    expected = ("Basic " + base64.b64encode(f"{ADAPTER_USERNAME}:{token}".encode()).decode()) if token else ""
    repo_prefix = f"{PREFIX}{key}/"

    class Handler(BaseHTTPRequestHandler):
        server_version = "uc-repo-adapter"

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - http.server's signature
            sys.stderr.write(f"repo-adapter: {self.address_string()} {format % args}\n")

        def _send(self, status: int, body: bytes = b"", content_type: str = "application/octet-stream") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _refuse(self, *, authenticate: bool) -> bool:
            """True when the request was answered already: idle adapter, or no valid credentials.

            Only the listing is authenticated. Upstream cannot answer an authentication challenge for
            file reads: its Authenticator dereferences the requesting site, which Java's HttpClient
            always passes as null. GETs serve metadata only, on the internal repo network (spec §5.3).
            """
            if adapter is None:
                self._send(503, b"REPO_ADAPTER is off\n", "text/plain")
                return True
            if authenticate and not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="repo-adapter"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True
            return False

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._send(200, b"ok\n", "text/plain")
            if self._refuse(authenticate=False):
                return None
            assert adapter is not None
            if not path.startswith(repo_prefix):
                return self._send(404)
            archive, separator, entry = urllib.parse.unquote(path[len(repo_prefix):]).partition("!/")
            try:
                if not separator:
                    data = adapter.pom(archive) if archive.endswith(".pom") else None
                elif entry == MANIFEST and archive.endswith((".hpi", ".jpi", ".war")):
                    data = adapter.manifest(archive)
                elif entry == "index.jelly" and archive.endswith(".jar"):
                    data = adapter.description(archive)
                else:
                    self.log_message("refused archive entry %s!/%s", archive, entry)
                    data = None
            except (BackendError, ArchiveError) as exc:
                self.log_message("GET %s failed: %s", path, exc)
                return self._send(502, str(exc).encode(), "text/plain")
            return self._send(200, data) if data is not None else self._send(404)

        do_HEAD = do_GET

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != PREFIX + "api/search/aql":
                return self._send(404)
            if self._refuse(authenticate=True):
                return None
            assert adapter is not None
            length = int(self.headers.get("Content-Length") or 0)
            if length > QUERY_LIMIT:
                return self._send(413)
            query = self.rfile.read(length).decode("utf-8", "replace")
            if query != AQL_TEMPLATE % key:
                return self._send(400, b"unsupported query\n", "text/plain")
            try:
                rows = adapter.refresh().rows
            except (BackendError, ArchiveError) as exc:
                self.log_message("listing failed: %s", exc)
                return self._send(502, str(exc).encode(), "text/plain")
            body = {"results": rows, "range": {"start_pos": 0, "end_pos": len(rows), "total": len(rows)}}
            return self._send(200, json.dumps(body).encode(), "application/json")

    return Handler


def create_adapter(settings: Settings) -> Adapter:
    username, password = load_repository_credentials(settings)
    http = Http(settings.repo_ca_file)
    max_bytes = settings.repo_max_archive_mb * 1024 * 1024
    backend: Backend
    if settings.repo_type == "artifact-keeper":
        backend = ArtifactKeeperBackend(http, settings.repo_url, settings.repo_key, username, password,
                                        settings.repo_adapter_dir / "tmp", max_bytes)
    else:
        backend = ArtifactoryBackend(http, settings.repo_url, settings.repo_api_url, settings.repo_key,
                                     username, password, max_bytes)
    return Adapter(backend, Cache(settings.repo_adapter_dir / "cache"), key=settings.repo_key,
                   report_file=settings.repo_adapter_report_file)


def make_server(settings: Settings, port: int = PORT, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    if not settings.repo_adapter:
        return ThreadingHTTPServer((host, port), make_handler(None, key=settings.repo_key, token=None))
    adapter = create_adapter(settings)
    token = ensure_token(settings.repo_adapter_token_file)
    return ThreadingHTTPServer((host, port), make_handler(adapter, key=settings.repo_key, token=token))


def serve(settings: Settings, port: int = PORT) -> None:
    server = make_server(settings, port)
    state = f"{settings.repo_type} repository {settings.repo_key} at {settings.repo_url}" if settings.repo_adapter \
        else "idle (REPO_ADAPTER is off)"
    print(f"repo-adapter: listening on :{port}, {state}", flush=True)
    server.serve_forever()
