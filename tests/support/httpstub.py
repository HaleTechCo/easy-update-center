"""A threaded HTTP server that answers with canned responses and records every request.

A route is (status, headers, body), or a callable taking (headers, body) and returning one.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

Reply = tuple[int, dict[str, str], bytes]
Route = Reply | Callable[[dict[str, str], bytes], Reply]
_RANGE = re.compile(r'^bytes=(\d*)-(\d*)$')


def serve_bytes(data: bytes, *, ranges: bool = True) -> Callable[[dict[str, str], bytes], Reply]:
    """A route serving data, honouring single `Range: bytes=a-b` / `bytes=-n` requests when ranges is set."""

    def route(headers: dict[str, str], body: bytes) -> Reply:
        match = _RANGE.match(headers.get('Range', '')) if ranges else None
        if not match:
            return 200, {}, data
        first, last = match.groups()
        if first == '':
            start, end = max(0, len(data) - int(last)), len(data) - 1
        else:
            start, end = int(first), min(int(last) if last else len(data) - 1, len(data) - 1)
        return 206, {'Content-Range': f'bytes {start}-{end}/{len(data)}'}, data[start : end + 1]

    return route


class StubServer:
    def __init__(self, routes: dict[tuple[str, str], Route]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                headers = dict(self.headers)
                stub.requests.append((self.command, self.path, headers, body))
                route = stub.routes.get((self.command, self.path), (404, {}, b''))
                status, reply_headers, payload = route(headers, body) if callable(route) else route
                self.send_response(status)
                for key, value in reply_headers.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle  # noqa: N815 - http.server dispatches on the do_<METHOD> name
            do_POST = _handle  # noqa: N815 - same

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
