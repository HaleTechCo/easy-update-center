"""The few small entries the adapter reads from untrusted archives.

See docs/architecture/repo-adapter.md#limits.
"""

from __future__ import annotations

import datetime as dt
import io
import struct
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

from uc.doctor import parse_manifest
from uc.errors import UcError

if TYPE_CHECKING:
    from uc.pom import Coordinates

SMALL_ENTRY_LIMIT = 1024 * 1024  # a POM, MANIFEST.MF or index.jelly
INNER_JAR_LIMIT = 64 * 1024 * 1024
CENTRAL_DIRECTORY_LIMIT = 16 * 1024 * 1024
EARLIEST_RELEASE = dt.datetime(2004, 1, 1, tzinfo=dt.UTC)
MANIFEST = 'META-INF/MANIFEST.MF'
_EOCD = b'PK\x05\x06'
_EOCD_SIZE = 22  # the end of central directory record, without its comment
_MAX_COMMENT_LENGTH = 0xFFFF
# an end record field whose value is in the zip64 end record
_ZIP64_SIZE_MARKER = 0xFFFFFFFF
_ZIP64_COUNT_MARKER = 0xFFFF
_ZIP64_LOCATOR = b'PK\x06\x07'
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD = b'PK\x06\x06'
_ZIP64_EOCD_SIZE = 56  # without its extensible data
_EXTENDED_TIMESTAMP = 0x5455  # extra field header id
# an extended-timestamp body starts with a flags byte, then the 4-byte mtime
_MTIME_END = 5

EntryReader = Callable[[str, int], 'bytes | None']


class ArchiveError(UcError):
    """An archive, or an entry in it, is unreadable or over a size limit."""


@dataclass(frozen=True)
class ArchiveFacts:
    """What the listing needs from one archive, read once and cached by its sha256."""

    manifest: bytes | None
    # from the manifest; None for a WAR or an incomplete manifest
    coordinates: Coordinates | None
    pom: bytes | None  # META-INF/maven/<groupId>/<artifactId>/pom.xml
    description: bytes | None  # index.jelly (plugins only)
    released: dt.datetime | None  # rule 9's archive date, before the plausibility check


def central_directory_size(stream: BinaryIO) -> int:
    """Size in bytes of a zip's central directory, as zipfile may read it.

    Read from the end records without parsing the directory. zipfile takes the zip64
    end record whenever a zip64 locator precedes the end record, whatever the classic
    record says; CPython versions differ in where they look for that record (just
    before the locator, or at the locator's offset), so both are read and the largest
    size found counts.
    """
    end = stream.seek(0, io.SEEK_END)
    tail_length = min(end, _EOCD_SIZE + _MAX_COMMENT_LENGTH)
    tail_start = end - tail_length
    stream.seek(tail_start)
    tail = stream.read(tail_length)
    at = tail.rfind(_EOCD)
    if at < 0 or len(tail) - at < _EOCD_SIZE:
        msg = 'not a zip archive (no end of central directory record)'
        raise ArchiveError(msg)
    entries, size = struct.unpack('<HI', tail[at + 10 : at + 16])
    markers = size == _ZIP64_SIZE_MARKER or entries == _ZIP64_COUNT_MARKER
    sizes = [] if size == _ZIP64_SIZE_MARKER else [size]
    locator_at = tail_start + at - _ZIP64_LOCATOR_SIZE
    locator = b''
    if locator_at >= 0:
        stream.seek(locator_at)
        locator = stream.read(_ZIP64_LOCATOR_SIZE)
    if locator.startswith(_ZIP64_LOCATOR):
        (record_offset,) = struct.unpack('<Q', locator[8:16])
        for offset in {locator_at - _ZIP64_EOCD_SIZE, record_offset}:
            if offset < 0:
                continue
            stream.seek(offset)
            record = stream.read(_ZIP64_EOCD_SIZE)
            if len(record) == _ZIP64_EOCD_SIZE and record.startswith(_ZIP64_EOCD):
                sizes.append(struct.unpack('<Q', record[40:48])[0])
    elif markers:
        msg = 'zip64 archive without a zip64 end record locator'
        raise ArchiveError(msg)
    if not sizes:
        msg = 'zip64 end of central directory record not found'
        raise ArchiveError(msg)
    return max(sizes)


def open_archive(stream: BinaryIO) -> zipfile.ZipFile:
    """Open a ZipFile over a seekable stream.

    Refuses central directories over CENTRAL_DIRECTORY_LIMIT.
    """
    size = central_directory_size(stream)
    if size > CENTRAL_DIRECTORY_LIMIT:
        msg = f'central directory of {size} bytes exceeds {CENTRAL_DIRECTORY_LIMIT}'
        raise ArchiveError(msg)
    stream.seek(0)
    try:
        return zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
        msg = f'unreadable archive: {exc}'
        raise ArchiveError(msg) from exc


def read_entry(archive: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """Read an entry's bytes.

    None when absent, ArchiveError when it exceeds limit, whatever its header says.
    """
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        msg = f'{name} is larger than {limit} bytes'
        raise ArchiveError(msg)
    try:
        with archive.open(info) as entry:
            data = entry.read(limit + 1)
    except (
        zipfile.BadZipFile,
        OSError,
        ValueError,
        NotImplementedError,
        RuntimeError,
        zlib.error,
        EOFError,
    ) as exc:
        msg = f'unreadable entry {name}: {exc}'
        raise ArchiveError(msg) from exc  # RuntimeError: encrypted
    if len(data) > limit:
        msg = f'{name} is larger than {limit} bytes'
        raise ArchiveError(msg)
    return data


def zip_reader(archive: zipfile.ZipFile) -> EntryReader:
    """Return an EntryReader that serves entries from a local zip."""
    return lambda name, limit: read_entry(archive, name, limit)


def _extended_mtime(extra: bytes) -> dt.datetime | None:
    """Return the mtime of a 0x5455 extended-timestamp field, which is UTC.

    Only when the entry carries one.
    """
    at = 0
    while at + 4 <= len(extra):
        header, size = struct.unpack('<HH', extra[at : at + 4])
        body = extra[at + 4 : at + 4 + size]
        if header == _EXTENDED_TIMESTAMP and len(body) >= _MTIME_END and body[0] & 1:
            (seconds,) = struct.unpack('<i', body[1:_MTIME_END])
            return dt.datetime.fromtimestamp(seconds, dt.UTC)
        at += 4 + size
    return None


def archive_date(archive: zipfile.ZipFile) -> dt.datetime | None:
    """META-INF/MANIFEST.MF's time.

    Its UTC extended timestamp, else its DOS time read as UTC.
    """
    try:
        info = archive.getinfo(MANIFEST)
    except KeyError:
        return None
    extended = _extended_mtime(info.extra)
    if extended is not None:
        return extended
    try:
        return dt.datetime(*info.date_time, tzinfo=dt.UTC)
    except ValueError:
        return None


def plausible(released: dt.datetime | None, repository_time: dt.datetime) -> str | None:
    """None when the archive date may be used (rule 9).

    Otherwise why the repository's time is used.
    """
    if released is None:
        return 'archive has no readable date'
    if released < EARLIEST_RELEASE:
        return f'archive date {released:%Y-%m-%d} is before 2004'
    if released > repository_time + dt.timedelta(days=1):
        return f'archive date {released:%Y-%m-%d} is after the upload'
    return None


def manifest_coordinates(manifest: bytes | None) -> Coordinates | None:
    """Return a plugin's coordinates from its manifest.

    From its Group-Id, Artifact-Id and Plugin-Version; None when any is missing.
    """
    if manifest is None:
        return None
    attributes = parse_manifest(manifest.decode('utf-8', 'replace'))
    group, artifact = attributes.get('Group-Id'), attributes.get('Artifact-Id')
    version = attributes.get('Plugin-Version', '').split(' ')[0]
    if not group or not artifact or not version:
        return None
    return group, artifact, version


def jar_description(jar: bytes) -> bytes | None:
    """Return the index.jelly of a jar held in memory, None when it has none."""
    return read_entry(open_archive(io.BytesIO(jar)), 'index.jelly', SMALL_ENTRY_LIMIT)


def find_description(read: EntryReader, artifact_id: str, version: str) -> bytes | None:
    """index.jelly where Maven, Gradle and pre-2015 builds put it (rule 6)."""
    for inner in (
        f'WEB-INF/lib/{artifact_id}.jar',
        f'WEB-INF/lib/{artifact_id}-{version}.jar',
    ):
        jar = read(inner, INNER_JAR_LIMIT)
        if jar is not None:
            description = jar_description(jar)
            if description is not None:
                return description
    return read('WEB-INF/classes/index.jelly', SMALL_ENTRY_LIMIT)


def extract_facts(
    read: EntryReader,
    path_coordinates: Coordinates,
    *,
    plugin: bool,
    released: dt.datetime | None,
) -> ArchiveFacts:
    """Read an archive's facts through `read`.

    `read` serves entries from a local zip or `!/` requests.
    """
    manifest = read(MANIFEST, SMALL_ENTRY_LIMIT)
    coordinates = manifest_coordinates(manifest) if plugin else None
    group, artifact, version = coordinates or path_coordinates
    pom = read(f'META-INF/maven/{group}/{artifact}/pom.xml', SMALL_ENTRY_LIMIT)
    description = find_description(read, artifact, version) if plugin else None
    return ArchiveFacts(manifest, coordinates, pom, description, released)
