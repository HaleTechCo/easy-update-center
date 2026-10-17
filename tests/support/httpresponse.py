"""A response read in full, for the tests that talk to a server over a connection.

Redirects are never followed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import http.client


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


def exchange(
    connection: http.client.HTTPConnection,
    method: str,
    path: str,
    headers: dict[str, str],
) -> Response:
    """Send one request on connection, read the answer in full and close it."""
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return Response(
            response.status,
            {k.lower(): v for k, v in response.getheaders()},
            response.read(),
        )
    finally:
        connection.close()
