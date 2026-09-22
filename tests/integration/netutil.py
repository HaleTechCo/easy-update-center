"""HTTP helpers for integration tests."""
from __future__ import annotations

import http.client
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def location(self) -> str | None:
        return self.headers.get("location")


def fetch(url: str, headers: dict[str, str] | None = None) -> Response:
    """GET without following redirects."""
    parts = urlsplit(url)
    connection_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parts.hostname, parts.port, timeout=30)
    try:
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        connection.request("GET", path or "/", headers={"User-Agent": "uc-integration-test", **(headers or {})})
        response = connection.getresponse()
        return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
    finally:
        connection.close()


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "uc-integration-test"}),
                                timeout=120) as response:
        return response.read()


def wait_for(url: str, timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if fetch(url).status == 200:
                return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"{url} did not answer 200 within {timeout}s")
        time.sleep(2)
