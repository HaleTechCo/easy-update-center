"""Small HTTP client helpers for tests."""

from __future__ import annotations

import urllib.error
import urllib.request


def put(url: str, data: bytes, auth: str | None = None) -> int:
    """Make a PUT request to url with optional Authorization header; return status code including error statuses."""
    request = urllib.request.Request(url, data=data, method='PUT', headers={'Authorization': auth} if auth else {})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
