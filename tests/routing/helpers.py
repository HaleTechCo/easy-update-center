"""HTTP helper for web-tier routing tests; redirects are never followed."""
from __future__ import annotations

import http.client
from dataclasses import dataclass


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def location(self) -> str | None:
        return self.headers.get("location")


def fetch(host: str, path: str, headers: dict[str, str] | None = None) -> Response:
    connection = http.client.HTTPConnection(host, 8080, timeout=10)
    try:
        connection.request("GET", path, headers={"User-Agent": "uc-routing-test", **(headers or {})})
        response = connection.getresponse()
        return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
    finally:
        connection.close()
