"""Artifactory and Artifact Keeper, as the adapter needs them (repo-adapter spec §2, §4.1)."""

from __future__ import annotations

import base64
import datetime as dt
import http.client
import io
import json
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Protocol
from urllib.parse import urlsplit

from uc import http as uc_http
from uc.aql import AQL_TEMPLATE
from uc.errors import UcError

from .archive import (
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

TIMEOUT = 60
LONG_TIMEOUT = 600
LISTING_LIMIT = 256 * 1024 * 1024
RANGE_BLOCK = 256 * 1024
LISTED_EXTENSIONS = ('.hpi', '.jpi', '.war', '.pom')
_SHA1 = re.compile(r'^[0-9a-f]{40}$')


class BackendError(UcError):
    pass


@dataclass(frozen=True)
class RepoEntry:
    directory: str  # org/jenkins-ci/plugins/git/5.2.0
    name: str  # git-5.2.0.hpi
    sha256: str
    sha1: str | None  # None when the backend's listing has none (Artifact Keeper)
    size: int
    created: str  # Artifactory's time format, as the repository reports it
    modified: str

    @property
    def path(self) -> str:
        return f'{self.directory}/{self.name}'

    @property
    def extension(self) -> str:
        return self.name.rsplit('.', 1)[-1]


def artifactory_time(moment: dt.datetime) -> str:
    """The time format Artifactory's AQL uses, which upstream parses: UTC with milliseconds."""
    utc = moment.astimezone(dt.UTC)
    return utc.strftime('%Y-%m-%dT%H:%M:%S.') + f'{utc.microsecond // 1000:03d}Z'


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def forwards_credentials(old_url: str, new_url: str) -> bool:
    """Whether a redirect from old_url to new_url may carry the repository credentials: only to the same
    host, and never from https to http, where they would cross the network in the clear.
    """
    old, new = urlsplit(old_url), urlsplit(new_url)
    return new.hostname == old.hostname and not (old.scheme == 'https' and new.scheme != 'https')


class _NoCredentialsAcrossHosts(urllib.request.HTTPRedirectHandler):
    """Artifactory redirects downloads to object storage; repository credentials must not follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: PLR0917 - urllib.request.HTTPRedirectHandler's signature
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not forwards_credentials(req.full_url, newurl):
            new.remove_header('Authorization')
        return new


class Http:
    def __init__(self, ca_file: Path | None) -> None:
        # REPO_CA_FILE adds to the system bundle: a repository behind a private CA may still redirect
        # downloads to object storage with a public certificate.
        self.context = ssl.create_default_context()
        if ca_file:
            self.context.load_verify_locations(cafile=str(ca_file))
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.context), _NoCredentialsAcrossHosts())

    def request(self, url: str, *, headers: dict[str, str], limit: int, data: bytes | None = None, method: str | None = None, timeout: float = TIMEOUT, truncate: bool = False) -> Response:
        """One request; the body is read up to limit bytes (BackendError beyond, unless truncate)."""
        request = urllib.request.Request(url, data=data, method=method, headers={'User-Agent': uc_http.USER_AGENT, **headers})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read(limit + 1)
                status, got = response.status, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            status, got = exc.code, dict(exc.headers.items())
            try:
                body = exc.read(4096)
            except (OSError, http.client.HTTPException):
                body = b''
        except (OSError, ValueError, http.client.HTTPException) as exc:
            msg = f'{method or "GET"} {url} failed: {exc!r}'
            raise BackendError(msg) from exc
        if len(body) > limit and not truncate:
            msg = f'{url} is larger than {limit} bytes'
            raise BackendError(msg)
        return Response(status, {k.lower(): v for k, v in got.items()}, body[:limit] if truncate else body)

    def download(self, url: str, *, headers: dict[str, str], out: BinaryIO, limit: int) -> None:
        request = urllib.request.Request(url, headers={'User-Agent': uc_http.USER_AGENT, **headers})
        try:
            with self.opener.open(request, timeout=LONG_TIMEOUT) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        msg = f'{url} is larger than {limit} bytes'
                        raise BackendError(msg)
                    out.write(chunk)
        except urllib.error.HTTPError as exc:
            msg = f'GET {url} failed with HTTP {exc.code}'
            raise BackendError(msg) from exc
        except (OSError, ValueError, http.client.HTTPException) as exc:
            msg = f'GET {url} failed: {exc!r}'
            raise BackendError(msg) from exc


class RangeReader(io.RawIOBase):
    """A seekable, read-only view of a remote file, fetched in cached blocks with Range requests."""

    def __init__(self, http: Http, url: str, headers: dict[str, str], size: int) -> None:
        self.http, self.url, self.headers, self.size = http, url, headers, size
        self.pos = 0
        self.blocks: dict[int, bytes] = {}

    @staticmethod
    def supported(http: Http, url: str, headers: dict[str, str]) -> bool:
        response = http.request(url, headers={**headers, 'Range': 'bytes=0-0'}, limit=1, truncate=True)
        return response.status == 206

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _block(self, index: int) -> bytes:
        if index not in self.blocks:
            start = index * RANGE_BLOCK
            end = min(start + RANGE_BLOCK, self.size) - 1
            response = self.http.request(self.url, headers={**self.headers, 'Range': f'bytes={start}-{end}'}, limit=RANGE_BLOCK)
            if response.status != 206:
                msg = f'{self.url} answered a Range request with HTTP {response.status}'
                raise BackendError(msg)
            self.blocks[index] = response.body
        return self.blocks[index]

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

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


class Backend(Protocol):
    type: str

    def list(self) -> list[RepoEntry]: ...
    def get(self, path: str) -> bytes | None: ...
    def sha1(self, entry: RepoEntry) -> str: ...
    def facts(self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool) -> ArchiveFacts: ...
    def jar_description(self, jar_path: str) -> bytes | None: ...


def _basic(username: str, password: str) -> dict[str, str]:
    return {'Authorization': 'Basic ' + base64.b64encode(f'{username}:{password}'.encode()).decode()}


def _quote(path: str) -> str:
    return urllib.parse.quote(path, safe='/!+-._~')


class ArtifactoryBackend:
    type = 'artifactory'

    def __init__(self, http: Http, url: str, api_url: str, key: str, *, username: str, password: str, max_archive_bytes: int) -> None:
        self.http, self.api_url, self.key = http, api_url, key
        self.base = f'{url}{key}/'
        self.auth = _basic(username, password)
        self.max_archive_bytes = max_archive_bytes

    def list(self) -> list[RepoEntry]:
        response = self.http.request(
            self.api_url + 'search/aql',
            method='POST',
            data=(AQL_TEMPLATE % self.key).encode(),
            headers={**self.auth, 'Content-Type': 'text/plain', 'Accept': 'application/json'},
            limit=LISTING_LIMIT,
            timeout=LONG_TIMEOUT,
        )
        if response.status != 200:
            msg = f'AQL query to {self.api_url} failed with HTTP {response.status}'
            raise BackendError(msg)
        try:
            results = json.loads(response.body)['results']
            return [RepoEntry(r['path'], r['name'], r['sha256'], r.get('actual_sha1'), int(r.get('size') or 0), r.get('created') or r['modified'], r['modified']) for r in results]
        except (ValueError, KeyError, TypeError) as exc:
            msg = f'unexpected AQL response from {self.api_url}: {exc}'
            raise BackendError(msg) from exc

    def get(self, path: str) -> bytes | None:
        response = self.http.request(self.base + _quote(path), headers=self.auth, limit=self.max_archive_bytes, timeout=LONG_TIMEOUT)
        if response.status == 404:
            return None
        if response.status != 200:
            msg = f'GET {self.base}{path} failed with HTTP {response.status}'
            raise BackendError(msg)
        return response.body

    def entry(self, path: str, name: str, limit: int) -> bytes | None:
        response = self.http.request(f'{self.base}{_quote(path)}!/{_quote(name)}', headers=self.auth, limit=limit)
        if response.status == 404:
            return None
        if response.status != 200:
            msg = f'GET {self.base}{path}!/{name} failed with HTTP {response.status}'
            raise BackendError(msg)
        return response.body

    def sha1(self, entry: RepoEntry) -> str:
        if not entry.sha1 or not _SHA1.match(entry.sha1):
            msg = f'no sha1 for {entry.path} in the AQL listing'
            raise BackendError(msg)
        return entry.sha1

    def archive_released(self, entry: RepoEntry) -> dt.datetime | None:
        """The archive date from its central directory via Range requests; None without Range support."""
        url = self.base + _quote(entry.path)
        if not RangeReader.supported(self.http, url, self.auth):
            return None
        return archive_date(open_archive(RangeReader(self.http, url, self.auth, entry.size)))

    def facts(self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool) -> ArchiveFacts:
        return extract_facts(lambda name, limit: self.entry(entry.path, name, limit), path_coordinates, plugin=plugin, released=self.archive_released(entry))

    def jar_description(self, jar_path: str) -> bytes | None:
        return self.entry(jar_path, 'index.jelly', SMALL_ENTRY_LIMIT)


class ArtifactKeeperBackend:
    type = 'artifact-keeper'

    def __init__(self, http: Http, url: str, key: str, *, username: str, token: str, tmp_dir: Path, max_archive_bytes: int) -> None:
        self.http, self.key, self.tmp_dir = http, key, tmp_dir
        self.api = f'{url}api/v1/repositories/{key}/artifacts'
        self.base = f'{url}maven/{key}/'
        self.bearer = {'Authorization': f'Bearer {token}'}
        self.basic = _basic(username, token)
        self.max_archive_bytes = max_archive_bytes

    def _page(self, cursor: str | None, page: int) -> dict:
        query = {'per_page': '100', **({'cursor': cursor} if cursor else {'page': str(page)})}
        response = self.http.request(f'{self.api}?{urllib.parse.urlencode(query)}', headers={**self.bearer, 'Accept': 'application/json'}, limit=LISTING_LIMIT)
        if response.status != 200:
            msg = f'listing {self.api} failed with HTTP {response.status}'
            raise BackendError(msg)
        try:
            return json.loads(response.body)
        except ValueError as exc:
            msg = f'unexpected listing response from {self.api}: {exc}'
            raise BackendError(msg) from exc

    def list(self) -> list[RepoEntry]:
        entries: list[RepoEntry] = []
        cursor, page = None, 1
        while True:
            body = self._page(cursor, page)
            try:
                for item in body['items']:
                    directory, _, name = item['path'].rpartition('/')
                    if name.endswith((*LISTED_EXTENSIONS, '.jar')) and directory:
                        created = artifactory_time(parse_time(item['created_at']))
                        entries.append(RepoEntry(directory, name, item['checksum_sha256'], None, int(item['size_bytes']), created, created))
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

    def get(self, path: str) -> bytes | None:
        response = self.http.request(self.base + _quote(path), headers=self.basic, limit=self.max_archive_bytes, timeout=LONG_TIMEOUT)
        if response.status == 404:
            return None
        if response.status != 200:
            msg = f'GET {self.base}{path} failed with HTTP {response.status}'
            raise BackendError(msg)
        return response.body

    def sha1(self, entry: RepoEntry) -> str:
        data = self.get(entry.path + '.sha1')
        value = (data or b'').decode('ascii', 'replace').strip().split(' ')[0].lower()
        if not _SHA1.match(value):
            msg = f'no valid sha1 file for {entry.path}'
            raise BackendError(msg)
        return value

    def facts(self, entry: RepoEntry, path_coordinates: Coordinates, *, plugin: bool) -> ArchiveFacts:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=self.tmp_dir) as out:
            self.http.download(self.base + _quote(entry.path), headers=self.basic, out=out, limit=self.max_archive_bytes)
            archive = open_archive(out)
            return extract_facts(zip_reader(archive), path_coordinates, plugin=plugin, released=archive_date(archive))

    def jar_description(self, jar_path: str) -> bytes | None:
        data = self.get(jar_path)
        if data is None:
            return None
        try:
            return jar_description(data)
        except ArchiveError:
            return None
