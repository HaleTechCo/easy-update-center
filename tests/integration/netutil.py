"""HTTP helpers for integration tests."""

from __future__ import annotations

import http.client
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

WAIT_FOR_SECONDS = 300


@dataclass
class Response:
    """A response read in full: status, lower-cased headers and body."""

    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def location(self) -> str | None:
        """The Location header, if any."""
        return self.headers.get('location')


def fetch(url: str) -> Response:
    """GET without following redirects."""
    parts = urlsplit(url)
    connection_class = (
        http.client.HTTPSConnection
        if parts.scheme == 'https'
        else http.client.HTTPConnection
    )
    connection = connection_class(parts.hostname, parts.port, timeout=30)
    try:
        path = parts.path + (f'?{parts.query}' if parts.query else '')
        connection.request(
            'GET',
            path or '/',
            headers={'User-Agent': 'uc-integration-test'},
        )
        response = connection.getresponse()
        return Response(
            response.status,
            {k.lower(): v for k, v in response.getheaders()},
            response.read(),
        )
    finally:
        connection.close()


def get(url: str) -> bytes:
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={'User-Agent': 'uc-integration-test'}),
        timeout=120,
    ) as response:
        return response.read()


def wait_for(url: str) -> None:
    deadline = time.monotonic() + WAIT_FOR_SECONDS
    while True:
        try:
            if fetch(url).status == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            msg = f'{url} did not answer 200 within {WAIT_FOR_SECONDS}s'
            raise TimeoutError(msg)
        time.sleep(2)
