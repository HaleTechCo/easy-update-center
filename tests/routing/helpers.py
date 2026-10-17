"""HTTP helper for web-tier routing tests; redirects are never followed."""

from __future__ import annotations

import http.client
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'support'))

from httpresponse import Response, exchange


def fetch(
    host: str,
    path: str,
    headers: dict[str, str] | None = None,
    method: str = 'GET',
) -> Response:
    return exchange(
        http.client.HTTPConnection(host, 8080, timeout=10),
        method,
        path,
        {'User-Agent': 'uc-routing-test', **(headers or {})},
    )
