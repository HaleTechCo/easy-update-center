"""Test-only stand-in for the Artifact Keeper 1.10 calls the repo-adapter makes (repo-adapter spec §2).

GET /api/v1/repositories/<key>/artifacts (Bearer only, at most 100 per page, keyset cursor) and
GET /maven/<key>/<path> (basic or Bearer; .sha1/.sha256/.md5 side files). Like the real one it ignores Range.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HASHES = {'.sha1': hashlib.sha1, '.sha256': hashlib.sha256, '.md5': hashlib.md5}
_SHA256: dict[tuple[str, int, int], str] = {}  # (path, size, mtime_ns) -> sha256: WARs are large


def sha256_of(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _SHA256:
        _SHA256[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return _SHA256[key]


def list_items(root: Path, key: str) -> list[dict]:
    base = root / key
    items = []
    for directory, _, names in os.walk(base):
        for name in names:
            path = Path(directory) / name
            created = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC)
            items.append(
                {
                    'id': hashlib.md5(str(path).encode(), usedforsecurity=False).hexdigest(),
                    'repository_key': key,
                    'path': path.relative_to(base).as_posix(),
                    'name': 'org',
                    'version': None,
                    'size_bytes': path.stat().st_size,
                    'checksum_sha256': sha256_of(path),
                    'content_type': 'application/octet-stream',
                    'download_count': 0,
                    'created_at': created.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
                }
            )
    return sorted(items, key=lambda item: item['path'])


def page(items: list[dict], query: dict[str, list[str]]) -> dict:
    per_page = max(1, min(100, int(query.get('per_page', ['20'])[0])))
    cursor = query.get('cursor', [None])[0]
    if cursor:
        after = base64.urlsafe_b64decode(cursor.encode()).decode()
        remaining = [item for item in items if item['path'] > after]
        number = 0
    else:
        number = max(1, int(query.get('page', ['1'])[0]))
        remaining = items[(number - 1) * per_page :]
    chunk, more = remaining[:per_page], len(remaining) > per_page
    body = {'items': chunk, 'pagination': {'page': number or 1, 'per_page': per_page, 'total': len(items), 'total_pages': -(-len(items) // per_page)}, 'has_more': more}
    if more:
        body['next_cursor'] = base64.urlsafe_b64encode(chunk[-1]['path'].encode()).decode()
    return body


def make_handler(root: Path, user: str, token: str, *, anonymous_read: bool):
    bearer = f'Bearer {token}'
    basic = 'Basic ' + base64.b64encode(f'{user}:{token}'.encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes = b'', content_type: str = 'application/octet-stream') -> None:
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path, _, raw_query = self.path.partition('?')
            auth = self.headers.get('Authorization', '')
            if path == '/health':
                return self._send(200, b'{"status":"healthy","version":"1.10.0"}', 'application/json')
            parts = path.strip('/').split('/')
            if len(parts) == 5 and parts[:3] == ['api', 'v1', 'repositories'] and parts[4] == 'artifacts':
                if auth != bearer:
                    return self._send(401, b'Authentication required', 'text/plain')
                body = page(list_items(root, parts[3]), urllib.parse.parse_qs(raw_query))
                return self._send(200, json.dumps(body).encode(), 'application/json')
            if len(parts) >= 3 and parts[0] == 'maven':
                if auth not in (bearer, basic) and not anonymous_read:
                    return self._send(401, b'Authentication required', 'text/plain')
                relative = urllib.parse.unquote('/'.join(parts[2:]))
                target = (root / parts[1] / relative).resolve()
                if not target.is_relative_to(root.resolve()):
                    return self._send(404)
                if target.is_file():
                    return self._send(200, target.read_bytes())
                suffix = target.suffix
                original = target.with_suffix('')
                if suffix in HASHES and original.is_file():
                    return self._send(200, HASHES[suffix](original.read_bytes()).hexdigest().encode(), 'text/plain')
                return self._send(404)
            return self._send(404, b'Repository not found', 'text/plain')

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='directory containing one folder per repository')
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--user', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--anonymous-read', action='store_true', help='allow /maven reads without credentials')
    args = parser.parse_args()
    handler = make_handler(args.root, args.user, args.token, anonymous_read=args.anonymous_read)
    ThreadingHTTPServer(('0.0.0.0', args.port), handler).serve_forever()


if __name__ == '__main__':
    main()
