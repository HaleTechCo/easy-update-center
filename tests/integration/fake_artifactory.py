"""Test-only stand-in for the Artifactory calls update-center2 makes, plus uploads (CONTRIBUTING.md#tests).

POST /artifactory/api/search/aql, GET /artifactory/<repo>/<path>, GET /artifactory/<repo>/<path>!/<entry>, PUT /artifactory/<repo>/<path> (201, or 409 when the path exists) and <path>.sha1.
update-center2 answers 401 challenges with credentials, so private reads use WWW-Authenticate: Basic.
File reads honour a single Range, as Artifactory does (the repo-adapter reads archive dates that way).
Checksum policy 'server' (the default) serves every file's <path>.sha1. 'client' is Artifactory's default for
local repositories: <path>.sha1 exists only when the upload sent X-Checksum-Sha1 (stored beside the file), an
upload whose X-Checksum-Sha1 does not match its body is refused with 409, and files put on disk get 404.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PREFIX = '/artifactory/'
EXTENSIONS = ('.hpi', '.jpi', '.war', '.pom')
_REPO = re.compile(r'"repo":\{"\$eq":"([A-Za-z0-9._-]+)"\}')
_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


def aql_results(root: Path, repository: str) -> list[dict]:
    base = root / repository
    results = []
    for directory, _, names in os.walk(base):
        for name in names:
            if not name.endswith(EXTENSIONS):
                continue
            path = Path(directory) / name
            data = path.read_bytes()
            stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            results.append(
                {
                    'repo': repository,
                    'path': path.parent.relative_to(base).as_posix(),
                    'name': name,
                    'modified': stamp,
                    'created': stamp,
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'actual_sha1': hashlib.sha1(data, usedforsecurity=False).hexdigest(),
                    'size': len(data),
                }
            )
    return sorted(results, key=lambda r: (r['path'], r['name']))


def read_artifact(root: Path, relative: str, *, server_checksums: bool = True) -> bytes | None:
    archive, separator, entry = relative.partition('!/')
    target = (root / archive).resolve()
    if not target.is_relative_to(root.resolve()):
        return None
    if server_checksums and not separator and not target.is_file() and target.suffix == '.sha1' and target.with_suffix('').is_file():
        return hashlib.sha1(target.with_suffix('').read_bytes(), usedforsecurity=False).hexdigest().encode()
    if not target.is_file():
        return None
    if not separator:
        return target.read_bytes()
    try:
        with zipfile.ZipFile(target) as zipped:
            return zipped.read(entry)
    except (KeyError, zipfile.BadZipFile):
        return None


def make_handler(root: Path, credentials: str, *, anonymous_read: bool, checksum_policy: str = 'server'):
    expected = 'Basic ' + base64.b64encode(credentials.encode()).decode()
    client = checksum_policy == 'client'

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes = b'', content_type: str = 'application/octet-stream', headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def _challenge(self) -> None:
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="Artifactory Realm"')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def _authorized(self) -> bool:
            return self.headers.get('Authorization') == expected

        def do_POST(self) -> None:
            if self.path != PREFIX + 'api/search/aql':
                return self._send(404)
            if not self._authorized():
                return self._challenge()
            query = self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode()
            match = _REPO.search(query)
            if not match:
                return self._send(400, b'unsupported query', 'text/plain')
            self._send(200, json.dumps({'results': aql_results(root, match.group(1))}).encode(), 'application/json')
            return None

        def do_GET(self) -> None:
            if not self.path.startswith(PREFIX):
                return self._send(404)
            if not anonymous_read and not self._authorized():
                return self._challenge()
            relative = urllib.parse.unquote(self.path[len(PREFIX) :].split('?', 1)[0])
            data = read_artifact(root, relative, server_checksums=not client)
            if data is None:
                return self._send(404)
            match = _RANGE.match(self.headers.get('Range', '')) if '!/' not in relative else None
            if match and data:
                first, last = match.groups()
                if first == '':
                    start, end = max(0, len(data) - int(last)), len(data) - 1
                else:
                    start, end = int(first), min(int(last) if last else len(data) - 1, len(data) - 1)
                return self._send(206, data[start : end + 1], headers={'Content-Range': f'bytes {start}-{end}/{len(data)}', 'Accept-Ranges': 'bytes'})
            self._send(200, data)
            return None

        def do_PUT(self) -> None:
            data = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            if not self.path.startswith(PREFIX):
                return self._send(404)
            if not self._authorized():
                return self._challenge()
            relative = urllib.parse.unquote(self.path[len(PREFIX) :].split('?', 1)[0])
            target = (root / relative).resolve()
            if '!/' in relative or not target.is_relative_to(root.resolve()):
                return self._send(400)
            if target.exists():
                return self._send(409, b'{"errors":[{"status":409,"message":"exists"}]}', 'application/json')
            claimed = (self.headers.get('X-Checksum-Sha1') or '').lower()
            actual = hashlib.sha1(data, usedforsecurity=False).hexdigest()
            if client and claimed and claimed != actual:
                return self._send(409, json.dumps({'errors': [{'status': 409, 'message': f'Checksum policy rejected the artifact: client sha1 {claimed}, actual sha1 {actual}'}]}).encode(), 'application/json')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if client and claimed:
                target.with_name(target.name + '.sha1').write_text(actual)
            return self._send(201)

        do_HEAD = do_GET  # noqa: N815 - http.server dispatches on the do_<METHOD> name

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='directory containing one folder per repository')
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--credentials', required=True, help='username:password')
    parser.add_argument('--anonymous-read', action='store_true', help='allow file reads without credentials')
    parser.add_argument('--checksum-policy', choices=('server', 'client'), default='server', help="client: serve <path>.sha1 only for uploads that sent X-Checksum-Sha1, as Artifactory's local repositories do by default")
    args = parser.parse_args()
    handler = make_handler(args.root, args.credentials, anonymous_read=args.anonymous_read, checksum_policy=args.checksum_policy)
    ThreadingHTTPServer(('0.0.0.0', args.port), handler).serve_forever()


if __name__ == '__main__':
    main()
