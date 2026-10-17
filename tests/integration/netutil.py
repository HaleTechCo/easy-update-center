"""HTTP helpers for integration tests."""

from __future__ import annotations

import http.client
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'support'))

from httpresponse import Response, exchange

WAIT_FOR_SECONDS = 300


def fetch(url: str) -> Response:
    """GET without following redirects."""
    parts = urlsplit(url)
    connection_class = (
        http.client.HTTPSConnection
        if parts.scheme == 'https'
        else http.client.HTTPConnection
    )
    connection = connection_class(parts.hostname, parts.port, timeout=30)
    path = parts.path + (f'?{parts.query}' if parts.query else '')
    return exchange(
        connection, 'GET', path or '/', {'User-Agent': 'uc-integration-test'}
    )


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
