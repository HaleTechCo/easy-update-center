"""The few small entries the adapter reads from untrusted archives (repo-adapter spec §4.2, §5.2)."""

from __future__ import annotations

import datetime as dt
import io
import struct
import zipfile
import zlib
from dataclasses import dataclass
from typing import BinaryIO, Callable

from ..doctor import parse_manifest
from ..errors import UcError
from ..pom import Coordinates

SMALL_ENTRY_LIMIT = 1024 * 1024  # a POM, MANIFEST.MF or index.jelly
INNER_JAR_LIMIT = 64 * 1024 * 1024
CENTRAL_DIRECTORY_LIMIT = 16 * 1024 * 1024
EARLIEST_RELEASE = dt.datetime(2004, 1, 1, tzinfo=dt.timezone.utc)
MANIFEST = 'META-INF/MANIFEST.MF'
_EOCD = b'PK\x05\x06'
_ZIP64_LOCATOR = b'PK\x06\x07'
_ZIP64_EOCD = b'PK\x06\x06'

EntryReader = Callable[[str, int], 'bytes | None']


class ArchiveError(UcError):
    pass


@dataclass(frozen=True)
class ArchiveFacts:
    """What the listing needs from one archive, read once and cached by its sha256."""

    manifest: bytes | None
    coordinates: Coordinates | None  # from the manifest; None for a WAR or an incomplete manifest
    pom: bytes | None  # META-INF/maven/<groupId>/<artifactId>/pom.xml
    description: bytes | None  # index.jelly (plugins only)
    released: dt.datetime | None  # rule 9's archive date, before the plausibility check


def central_directory_size(stream: BinaryIO) -> int:
    """Size in bytes of a zip's central directory, read from its end records without parsing it."""
    end = stream.seek(0, io.SEEK_END)
    tail_length = min(end, 22 + 65535)
    stream.seek(end - tail_length)
    tail = stream.read(tail_length)
    at = tail.rfind(_EOCD)
    if at < 0 or len(tail) - at < 22:
        raise ArchiveError('not a zip archive (no end of central directory record)')
    entries, size = struct.unpack('<HI', tail[at + 10 : at + 16])
    if size != 0xFFFFFFFF and entries != 0xFFFF:
        return size
    locator = tail[at - 20 : at] if at >= 20 else b''
    if not locator.startswith(_ZIP64_LOCATOR):
        raise ArchiveError('zip64 archive without a zip64 end record locator')
    (record_offset,) = struct.unpack('<Q', locator[8:16])
    stream.seek(record_offset)
    record = stream.read(56)
    if len(record) < 56 or not record.startswith(_ZIP64_EOCD):
        raise ArchiveError('zip64 end of central directory record not found')
    (size,) = struct.unpack('<Q', record[40:48])
    return size


def open_archive(stream: BinaryIO) -> zipfile.ZipFile:
    """A ZipFile over a seekable stream, refusing central directories over CENTRAL_DIRECTORY_LIMIT."""
    size = central_directory_size(stream)
    if size > CENTRAL_DIRECTORY_LIMIT:
        raise ArchiveError(f'central directory of {size} bytes exceeds {CENTRAL_DIRECTORY_LIMIT}')
    stream.seek(0)
    try:
        return zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
        raise ArchiveError(f'unreadable archive: {exc}') from exc


def read_entry(archive: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """The entry's bytes, None when absent; ArchiveError when it exceeds limit, whatever its header says."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ArchiveError(f'{name} is larger than {limit} bytes')
    try:
        with archive.open(info) as entry:
            data = entry.read(limit + 1)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError, zlib.error, EOFError) as exc:
        raise ArchiveError(f'unreadable entry {name}: {exc}') from exc  # RuntimeError: encrypted
    if len(data) > limit:
        raise ArchiveError(f'{name} is larger than {limit} bytes')
    return data


def zip_reader(archive: zipfile.ZipFile) -> EntryReader:
    return lambda name, limit: read_entry(archive, name, limit)


def _extended_mtime(extra: bytes) -> dt.datetime | None:
    """The mtime of a 0x5455 extended-timestamp field, which is UTC, when the entry carries one."""
    at = 0
    while at + 4 <= len(extra):
        header, size = struct.unpack('<HH', extra[at : at + 4])
        body = extra[at + 4 : at + 4 + size]
        if header == 0x5455 and len(body) >= 5 and body[0] & 1:
            (seconds,) = struct.unpack('<i', body[1:5])
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
        at += 4 + size
    return None


def archive_date(archive: zipfile.ZipFile) -> dt.datetime | None:
    """META-INF/MANIFEST.MF's time: its UTC extended timestamp, else its DOS time read as UTC."""
    try:
        info = archive.getinfo(MANIFEST)
    except KeyError:
        return None
    extended = _extended_mtime(info.extra)
    if extended is not None:
        return extended
    try:
        return dt.datetime(*info.date_time, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def plausible(released: dt.datetime | None, repository_time: dt.datetime) -> str | None:
    """None when the archive date may be used (rule 9); otherwise why the repository's time is used."""
    if released is None:
        return 'archive has no readable date'
    if released < EARLIEST_RELEASE:
        return f'archive date {released:%Y-%m-%d} is before 2004'
    if released > repository_time + dt.timedelta(days=1):
        return f'archive date {released:%Y-%m-%d} is after the upload'
    return None


def manifest_coordinates(manifest: bytes | None) -> Coordinates | None:
    if manifest is None:
        return None
    attributes = parse_manifest(manifest.decode('utf-8', 'replace'))
    group, artifact = attributes.get('Group-Id'), attributes.get('Artifact-Id')
    version = attributes.get('Plugin-Version', '').split(' ')[0]
    if not group or not artifact or not version:
        return None
    return group, artifact, version


def jar_description(jar: bytes) -> bytes | None:
    return read_entry(open_archive(io.BytesIO(jar)), 'index.jelly', SMALL_ENTRY_LIMIT)


def find_description(read: EntryReader, artifact_id: str, version: str) -> bytes | None:
    """index.jelly where Maven, Gradle and pre-2015 builds put it (rule 6)."""
    for inner in (f'WEB-INF/lib/{artifact_id}.jar', f'WEB-INF/lib/{artifact_id}-{version}.jar'):
        jar = read(inner, INNER_JAR_LIMIT)
        if jar is not None:
            description = jar_description(jar)
            if description is not None:
                return description
    return read('WEB-INF/classes/index.jelly', SMALL_ENTRY_LIMIT)


def extract_facts(read: EntryReader, path_coordinates: Coordinates, *, plugin: bool, released: dt.datetime | None) -> ArchiveFacts:
    """Read an archive's facts through `read`, which serves entries from a local zip or `!/` requests."""
    manifest = read(MANIFEST, SMALL_ENTRY_LIMIT)
    coordinates = manifest_coordinates(manifest) if plugin else None
    group, artifact, version = coordinates or path_coordinates
    pom = read(f'META-INF/maven/{group}/{artifact}/pom.xml', SMALL_ENTRY_LIMIT)
    description = find_description(read, artifact, version) if plugin else None
    return ArchiveFacts(manifest, coordinates, pom, description, released)
