"""Artifactory and Artifact Keeper, as the adapter needs them.

See docs/architecture/repo-adapter.md#requests-it-answers.
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, override

from uc.aql import AQL_TEMPLATE, LISTING_LIMIT
from uc.http import LONG_TIMEOUT, Http, HttpError

from .archive import (
    CENTRAL_DIRECTORY_LIMIT,
    INNER_JAR_LIMIT,
    SMALL_ENTRY_LIMIT,
    ArchiveError,
    ArchiveFacts,
    archive_date,
    extract_facts,
    jar_description,
    open_archive,
    zip_reader,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uc.pom import Coordinates

RANGE_BLOCK = 256 * 1024
RANGE_READ_LIMIT = CENTRAL_DIRECTORY_LIMIT + 1024 * 1024
"""The most one RangeReader fetches: a central directory, its end records, a margin."""
SHA1_FILE_LIMIT = 4096
"""A .sha1 file: 40 hex characters, sometimes followed by a file name."""
LISTED_EXTENSIONS = ('.hpi', '.jpi', '.war', '.pom')
_SHA1 = re.compile(r'^[0-9a-f]{40}$')


class BackendError(HttpError):
    """A repository answered with something the adapter cannot use.

    An Http failure is an HttpError, of which this is one kind, so callers catch
    HttpError.
    """


@dataclass(frozen=True)
class RepoEntry:
    """One file in the repository's listing, as the backend reports it."""

    directory: str  # org/jenkins-ci/plugins/git/5.2.0
    name: str  # git-5.2.0.hpi
    sha256: str
    sha1: str | None  # None when the backend's listing has none (Artifact Keeper)
    size: int
    created: str  # Artifactory's time format, as the repository reports it
    modified: str

    @property
    def path(self) -> str:
        """The file's path in the repository."""
        return f'{self.directory}/{self.name}'

    @property
    def extension(self) -> str:
        """The file name's last extension, without the dot."""
        return self.name.rsplit('.', 1)[-1]


def artifactory_time(moment: dt.datetime) -> str:
    """Format a moment the way Artifactory's AQL does, which upstream parses.

    UTC with milliseconds.
    """
    utc = moment.astimezone(dt.UTC)
    return utc.strftime('%Y-%m-%dT%H:%M:%S.') + f'{utc.microsecond // 1000:03d}Z'


def parse_time(value: str) -> dt.datetime:
    """Parse an ISO 8601 time from a repository listing."""
    return dt.datetime.fromisoformat(value)


class RangeReader(io.RawIOBase):
    """A seekable, read-only view of a remote file.

    Fetched in cached blocks with Range requests.
    """

    def __init__(
        self, http: Http, url: str, headers: dict[str, str], size: int
    ) -> None:
        """View the size bytes at url, sending headers with every request."""
        self.http, self.url, self.headers, self.size = http, url, headers, size
        self.pos = 0
        self.blocks: dict[int, bytes] = {}

    @staticmethod
    def supported(http: Http, url: str, headers: dict[str, str]) -> bool:
        """Return whether url answers a one-byte Range request.

        With 206 Partial Content.
        """
        response = http.request(
            url, headers={**headers, 'Range': 'bytes=0-0'}, limit=1, truncate=True
        )
        return response.status == HTTPStatus.PARTIAL_CONTENT

    @override
    def readable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return True

    @override
    def tell(self) -> int:
        return self.pos

    @override
    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _block(self, index: int) -> bytes:
        if index not in self.blocks:
            if (len(self.blocks) + 1) * RANGE_BLOCK > RANGE_READ_LIMIT:
                msg = (
                    f'{self.url}: more than {RANGE_READ_LIMIT} bytes read through '
                    'Range requests'
                )
                raise BackendError(msg)
            start = index * RANGE_BLOCK
            end = min(start + RANGE_BLOCK, self.size) - 1
            response = self.http.request(
                self.url,
                headers={**self.headers, 'Range': f'bytes={start}-{end}'},
                limit=RANGE_BLOCK,
            )
            if response.status != HTTPStatus.PARTIAL_CONTENT:
                msg = f'{self.url} answered a Range request with HTTP {response.status}'
                raise BackendError(msg)
            self.blocks[index] = response.body
        return self.blocks[index]

    @override
    def read(self, n: int = -1) -> bytes:
        end = self.size if n is None or n < 0 else min(self.size, self.pos + n)
        out = bytearray()
        while self.pos < end:
            block = self._block(self.pos // RANGE_BLOCK)
            offset = self.pos % RANGE_BLOCK
            chunk = block[offset : offset + (end - self.pos)]
            if not chunk:
                break
            out += chunk
            self.pos += len(chunk)
        return bytes(out)

    @override
    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


class Backend(Protocol):
    """What the listing and the server need from a repository."""

    type: str

    def list(self) -> list[RepoEntry]:
        """Return the repository's archives and POMs.

        Also its .jar files where the backend lists them.
        """

    def get(self, path: str, limit: int) -> bytes | None:
        """Return a file's bytes, at most limit (HttpError beyond).

        None when the repository does not have it.
        """

    def sha1(self, entry: RepoEntry) -> str:
        """Return a file's sha1; BackendError when the repository has none."""

    def facts(
        self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool
    ) -> ArchiveFacts:
        """Read what the listing needs from an archive.

        See docs/architecture/repo-adapter.md#filling-gaps.
        """

    def jar_description(self, jar_path: str) -> bytes | None:
        """Return the index.jelly of a repository .jar, None when there is none."""


def _basic(username: str, password: str) -> dict[str, str]:
    return {
        'Authorization': 'Basic '
        + base64.b64encode(f'{username}:{password}'.encode()).decode()
    }


def _quote(path: str) -> str:
    return urllib.parse.quote(path, safe='/!+-._~')


class ArtifactoryBackend:
    """Artifactory: upstream's own AQL query for the listing.

    `!/` requests for archive entries.
    """

    type = 'artifactory'

    def __init__(
        self,
        http: Http,
        url: str,
        api_url: str,
        key: str,
        *,
        username: str,
        password: str,
        max_archive_bytes: int,
    ) -> None:
        """Use repository key under url, and its REST API at api_url.

        A whole file is read up to max_archive_bytes.
        """
        self.http, self.api_url, self.key = http, api_url, key
        self.base = f'{url}{key}/'
        self.auth = _basic(username, password)
        self.max_archive_bytes = max_archive_bytes

    def list(self) -> list[RepoEntry]:
        """Return the archives and POMs that upstream's AQL query finds."""
        response = self.http.request(
            self.api_url + 'search/aql',
            method='POST',
            data=(AQL_TEMPLATE % self.key).encode(),
            headers={
                **self.auth,
                'Content-Type': 'text/plain',
                'Accept': 'application/json',
            },
            limit=LISTING_LIMIT,
            timeout=LONG_TIMEOUT,
        )
        if response.status != HTTPStatus.OK:
            msg = f'AQL query to {self.api_url} failed with HTTP {response.status}'
            raise BackendError(msg)
        try:
            results = json.loads(response.body)['results']
            return [
                RepoEntry(
                    r['path'],
                    r['name'],
                    r['sha256'],
                    r.get('actual_sha1'),
                    int(r.get('size') or 0),
                    r.get('created') or r['modified'],
                    r['modified'],
                )
                for r in results
            ]
        except (ValueError, KeyError, TypeError) as exc:
            msg = f'unexpected AQL response from {self.api_url}: {exc}'
            raise BackendError(msg) from exc

    def get(self, path: str, limit: int) -> bytes | None:
        """Return a file's bytes, at most limit (HttpError beyond); None on HTTP 404."""
        response = self.http.request(
            self.base + _quote(path),
            headers=self.auth,
            limit=limit,
            timeout=LONG_TIMEOUT,
        )
        if response.status == HTTPStatus.NOT_FOUND:
            return None
        if response.status != HTTPStatus.OK:
            msg = f'GET {self.base}{path} failed with HTTP {response.status}'
            raise BackendError(msg)
        return response.body

    def entry(self, path: str, name: str, limit: int) -> bytes | None:
        """Return one entry of the archive at path.

        Read through a `!/` request of up to limit bytes; None on HTTP 404.
        """
        response = self.http.request(
            f'{self.base}{_quote(path)}!/{_quote(name)}', headers=self.auth, limit=limit
        )
        if response.status == HTTPStatus.NOT_FOUND:
            return None
        if response.status != HTTPStatus.OK:
            msg = f'GET {self.base}{path}!/{name} failed with HTTP {response.status}'
            raise BackendError(msg)
        return response.body

    def sha1(self, entry: RepoEntry) -> str:
        """Return the sha1 from the AQL listing.

        BackendError when it has no valid one.
        """
        if not entry.sha1 or not _SHA1.match(entry.sha1):
            msg = f'no sha1 for {entry.path} in the AQL listing'
            raise BackendError(msg)
        return entry.sha1

    def archive_released(self, entry: RepoEntry) -> dt.datetime | None:
        """Read the archive date from its central directory via Range requests.

        None without Range support.
        """
        url = self.base + _quote(entry.path)
        if not RangeReader.supported(self.http, url, self.auth):
            return None
        return archive_date(
            open_archive(RangeReader(self.http, url, self.auth, entry.size))
        )

    def facts(
        self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool
    ) -> ArchiveFacts:
        """Read an archive's facts through `!/` and Range requests.

        Without downloading it.
        """
        return extract_facts(
            lambda name, limit: self.entry(entry.path, name, limit),
            path_coordinates,
            plugin=plugin,
            released=self.archive_released(entry),
        )

    def jar_description(self, jar_path: str) -> bytes | None:
        """Return a repository .jar's index.jelly through a `!/` request.

        None on HTTP 404.
        """
        return self.entry(jar_path, 'index.jelly', SMALL_ENTRY_LIMIT)


class ArtifactKeeperBackend:
    """Artifact Keeper: its REST API for the listing, its Maven endpoint for files."""

    type = 'artifact-keeper'

    def __init__(
        self,
        http: Http,
        url: str,
        key: str,
        *,
        username: str,
        token: str,
        tmp_dir: Path,
        max_archive_bytes: int,
    ) -> None:
        """Use repository key under url with username's token.

        Archives are downloaded into tmp_dir, up to max_archive_bytes.
        """
        self.http, self.key, self.tmp_dir = http, key, tmp_dir
        self.api = f'{url}api/v1/repositories/{key}/artifacts'
        self.base = f'{url}maven/{key}/'
        self.bearer = {'Authorization': f'Bearer {token}'}
        self.basic = _basic(username, token)
        self.max_archive_bytes = max_archive_bytes

    def _page(self, cursor: str | None, page: int) -> dict:
        query = {
            'per_page': '100',
            **({'cursor': cursor} if cursor else {'page': str(page)}),
        }
        response = self.http.request(
            f'{self.api}?{urllib.parse.urlencode(query)}',
            headers={**self.bearer, 'Accept': 'application/json'},
            limit=LISTING_LIMIT,
        )
        if response.status != HTTPStatus.OK:
            msg = f'listing {self.api} failed with HTTP {response.status}'
            raise BackendError(msg)
        try:
            return json.loads(response.body)
        except ValueError as exc:
            msg = f'unexpected listing response from {self.api}: {exc}'
            raise BackendError(msg) from exc

    def list(self) -> list[RepoEntry]:
        """Return the archives, POMs and .jar files.

        Through every page of the listing.
        """
        entries: list[RepoEntry] = []
        cursor, page = None, 1
        while True:
            body = self._page(cursor, page)
            try:
                for item in body['items']:
                    directory, _, name = item['path'].rpartition('/')
                    if name.endswith((*LISTED_EXTENSIONS, '.jar')) and directory:
                        created = artifactory_time(parse_time(item['created_at']))
                        entries.append(
                            RepoEntry(
                                directory,
                                name,
                                item['checksum_sha256'],
                                None,
                                int(item['size_bytes']),
                                created,
                                created,
                            )
                        )
            except (KeyError, TypeError, ValueError) as exc:
                msg = f'unexpected listing response from {self.api}: {exc}'
                raise BackendError(msg) from exc
            more = body.get('has_more')
            if more is None:
                pagination = body.get('pagination') or {}
                more = page < int(pagination.get('total_pages') or 0)
            if not more or not body['items']:
                return entries
            cursor, page = body.get('next_cursor'), page + 1

    def get(self, path: str, limit: int) -> bytes | None:
        """Return a file's bytes, at most limit (HttpError beyond); None on HTTP 404."""
        response = self.http.request(
            self.base + _quote(path),
            headers=self.basic,
            limit=limit,
            timeout=LONG_TIMEOUT,
        )
        if response.status == HTTPStatus.NOT_FOUND:
            return None
        if response.status != HTTPStatus.OK:
            msg = f'GET {self.base}{path} failed with HTTP {response.status}'
            raise BackendError(msg)
        return response.body

    def sha1(self, entry: RepoEntry) -> str:
        """Return the sha1 from the file's .sha1 companion.

        BackendError when it is missing or malformed.
        """
        data = self.get(entry.path + '.sha1', SHA1_FILE_LIMIT)
        value = (data or b'').decode('ascii', 'replace').strip().split(' ')[0].lower()
        if not _SHA1.match(value):
            msg = f'no valid sha1 file for {entry.path}'
            raise BackendError(msg)
        return value

    def facts(
        self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool
    ) -> ArchiveFacts:
        """Download the archive to a temporary file and read its facts there."""
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=self.tmp_dir) as out:
            self.http.download(
                self.base + _quote(entry.path),
                headers=self.basic,
                out=out,
                limit=self.max_archive_bytes,
            )
            archive = open_archive(out)
            return extract_facts(
                zip_reader(archive),
                path_coordinates,
                plugin=plugin,
                released=archive_date(archive),
            )

    def jar_description(self, jar_path: str) -> bytes | None:
        """Return a repository .jar's index.jelly.

        None when the jar is absent, unreadable or has none.
        """
        data = self.get(jar_path, INNER_JAR_LIMIT)
        if data is None:
            return None
        try:
            return jar_description(data)
        except ArchiveError:
            return None
