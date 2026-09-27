"""Downloads from repo.jenkins-ci.org (PROCUREMENT_SOURCE_URL) by Maven path, each checked against the .sha1 beside it."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from uc.repo_adapter.archive import SMALL_ENTRY_LIMIT
from uc.repo_adapter.backends import TIMEOUT, BackendError, Http

from . import ProcureError
from .staging import SHA1, sha1_of

if TYPE_CHECKING:
    from pathlib import Path

SIDE_FILE_LIMIT = 4096
POM_LIMIT = SMALL_ENTRY_LIMIT


class NotFoundError(ProcureError):
    """The source has no such file."""


def parse_sha1(body: bytes) -> str:
    """Return the digest a .sha1 file holds ('<digest>' or '<digest>  <name>', any case); raise ProcureError otherwise."""
    words = body.decode('ascii', 'replace').split()
    value = words[0].lower() if words else ''
    if not SHA1.match(value):
        msg = 'not a sha1 file'
        raise ProcureError(msg)
    return value


class Upstream:
    """Reads files by Maven path from a repository, anonymously."""

    def __init__(self, base_url: str, http: Http, *, limit: int) -> None:
        """Read from base_url (ending in /) through http, accepting no archive over limit bytes."""
        self.base, self.http, self.limit = base_url, http, limit

    def url(self, path: str) -> str:
        """Return path's URL at the source."""
        return self.base + path

    def sha1(self, path: str) -> str | None:
        """Return the sha1 the source publishes for path, None when it has no such file; raise ProcureError on anything else."""
        url = self.url(path) + '.sha1'
        try:
            response = self.http.request(url, headers={}, limit=SIDE_FILE_LIMIT, timeout=TIMEOUT)
        except BackendError as exc:
            raise ProcureError(str(exc)) from exc
        if response.status == HTTPStatus.NOT_FOUND:
            return None
        if response.status != HTTPStatus.OK:
            msg = f'GET {url} failed with HTTP {response.status}'
            raise ProcureError(msg)
        try:
            return parse_sha1(response.body)
        except ProcureError as exc:
            msg = f'{url} is {exc}'
            raise ProcureError(msg) from exc

    def fetch(self, path: str, part: Path) -> tuple[str, int]:
        """Download path into part and return its (sha1, size); NotFoundError when the source lacks it, ProcureError when it does not match its .sha1."""
        expected = self.sha1(path)
        if expected is None:
            msg = f'{self.url(path)} not found'
            raise NotFoundError(msg)
        limit = POM_LIMIT if path.endswith('.pom') else self.limit
        try:
            with part.open('wb') as out:
                self.http.download(self.url(path), headers={}, out=out, limit=limit)
        except BackendError as exc:
            raise ProcureError(str(exc)) from exc
        actual = sha1_of(part)
        if actual != expected:
            msg = f'{self.url(path)} does not match its .sha1 (got {actual}, published {expected})'
            raise ProcureError(msg)
        return actual, part.stat().st_size
