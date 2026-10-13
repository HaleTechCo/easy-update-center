"""Unit tests for uc.repo_adapter.archive.

Bounded zip reads, archive dates, descriptions and extracted facts.
"""

import datetime as dt
import io
import struct
import zipfile

import pytest

from archivefixture import make_hpi, make_war, pom_xml
from uc.repo_adapter.archive import (
    ArchiveError,
    archive_date,
    central_directory_size,
    extract_facts,
    find_description,
    manifest_coordinates,
    open_archive,
    plausible,
    read_entry,
    zip_reader,
)

UTC = dt.UTC
GIT = ('org.jenkins-ci.plugins', 'git', '5.2.0')


def zip_of(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def as_zip64(data: bytes) -> bytes:
    """Replace the end record with zip64 records.

    As archives with over 65535 entries have.
    """
    eocd = data.rfind(b'PK\x05\x06')
    entries, size, offset = struct.unpack('<HII', data[eocd + 10 : eocd + 20])
    body = data[:eocd]
    record = b'PK\x06\x06' + struct.pack(
        '<QHHIIQQQQ', 44, 45, 45, 0, 0, entries, entries, size, offset
    )
    locator = b'PK\x06\x07' + struct.pack('<IQI', 0, len(body), 1)
    end = b'PK\x05\x06' + struct.pack(
        '<HHHHIIH', 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0
    )
    return body + record + locator + end


def test_central_directory_size_plain_and_zip64():
    data = zip_of({'a.txt': b'hello', 'b.txt': b'world'})
    size = central_directory_size(io.BytesIO(data))
    assert 0 < size < len(data)
    assert central_directory_size(io.BytesIO(as_zip64(data))) == size
    assert open_archive(io.BytesIO(as_zip64(data))).read('a.txt') == b'hello'


def test_open_archive_refuses_non_zips_and_huge_central_directories():
    with pytest.raises(ArchiveError, match='not a zip archive'):
        open_archive(io.BytesIO(b'<html>404</html>'))
    data = bytearray(zip_of({'a.txt': b'x'}))
    eocd = data.rfind(b'PK\x05\x06')
    data[eocd + 12 : eocd + 16] = struct.pack('<I', 17 * 1024 * 1024)
    with pytest.raises(ArchiveError, match='central directory of 17825792 bytes'):
        open_archive(io.BytesIO(bytes(data)))


def test_read_entry_is_bounded():
    archive = open_archive(io.BytesIO(zip_of({'small': b'x' * 10, 'big': b'y' * 5000})))
    assert read_entry(archive, 'small', 10) == b'x' * 10
    assert read_entry(archive, 'missing', 10) is None
    with pytest.raises(ArchiveError, match='big is larger than 100 bytes'):
        read_entry(archive, 'big', 100)


def test_a_lying_size_header_is_refused():
    data = bytearray(
        zip_of({'entry': b'z' * 4096})
    )  # stored, so sizes are easy to patch
    central = data.find(b'PK\x01\x02')
    data[central + 20 : central + 28] = struct.pack(
        '<II', 16, 16
    )  # claim 16 bytes, compressed and not
    with pytest.raises(ArchiveError):
        read_entry(open_archive(io.BytesIO(bytes(data))), 'entry', 1024)


def test_corrupt_and_encrypted_entries_are_archive_errors():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('entry', b'x' * 4096)
    corrupt = bytearray(buffer.getvalue())
    local = corrupt.find(b'PK\x03\x04')
    name_length, extra_length = struct.unpack('<HH', corrupt[local + 26 : local + 30])
    start = local + 30 + name_length + extra_length
    corrupt[start : start + 8] = b'\xff' * 8  # an invalid deflate stream: zlib.error
    with pytest.raises(ArchiveError):
        read_entry(open_archive(io.BytesIO(bytes(corrupt))), 'entry', 1 << 20)
    encrypted = bytearray(zip_of({'entry': b'secret'}))
    for signature, flag in (
        (b'PK\x03\x04', 6),
        (b'PK\x01\x02', 8),
    ):  # bit 0 of the flags: encrypted
        encrypted[encrypted.find(signature) + flag] |= 1
    with pytest.raises(ArchiveError):  # zipfile raises RuntimeError without a password
        read_entry(open_archive(io.BytesIO(bytes(encrypted))), 'entry', 1 << 20)


def test_archive_date_prefers_the_extended_timestamp():
    stamp = int(dt.datetime(2023, 11, 18, 20, 14, tzinfo=UTC).timestamp())
    with_unix = open_archive(io.BytesIO(make_hpi(*GIT, unix_time=stamp)))
    assert archive_date(with_unix) == dt.datetime(2023, 11, 18, 20, 14, tzinfo=UTC)
    dos_only = open_archive(io.BytesIO(make_hpi(*GIT, date=(2013, 2, 25, 16, 44, 0))))
    assert archive_date(dos_only) == dt.datetime(2013, 2, 25, 16, 44, tzinfo=UTC)
    assert archive_date(open_archive(io.BytesIO(zip_of({'x': b'y'})))) is None


@pytest.mark.parametrize(
    ('released', 'reason'),
    [
        (dt.datetime(2024, 5, 1, tzinfo=UTC), None),
        (
            dt.datetime(2026, 9, 23, 6, tzinfo=UTC),
            None,
        ),  # up to a day after the upload: time zones
        (dt.datetime(1980, 2, 1, tzinfo=UTC), 'archive date 1980-02-01 is before 2004'),
        (
            dt.datetime(2026, 9, 25, tzinfo=UTC),
            'archive date 2026-09-25 is after the upload',
        ),
        (None, 'archive has no readable date'),
    ],
)
def test_plausible(released, reason):
    assert plausible(released, dt.datetime(2026, 9, 22, 23, tzinfo=UTC)) == reason


def test_manifest_coordinates():
    text = (
        b'Manifest-Version: 1.0\r\nGroup-Id: io.jenkins.plugins\r\n'
        b'Artifact-Id: caffeine-api\r\nPlugin-Version: 3.2.4 (private-x)\r\n\r\n'
    )
    assert manifest_coordinates(text) == ('io.jenkins.plugins', 'caffeine-api', '3.2.4')
    assert (
        manifest_coordinates(
            b'Manifest-Version: 1.0\r\nShort-Name: old\r\nPlugin-Version: 1.0\r\n\r\n'
        )
        is None
    )
    assert manifest_coordinates(None) is None


@pytest.mark.parametrize('layout', ['maven', 'gradle', 'classes'])
def test_description_is_found_wherever_the_build_put_it(layout):
    archive = open_archive(io.BytesIO(make_hpi(*GIT, layout=layout)))
    assert (
        find_description(zip_reader(archive), 'git', '5.2.0')
        == b'<div>An excellent plugin.</div>'
    )


def test_no_description_anywhere():
    archive = open_archive(io.BytesIO(make_hpi(*GIT, description=None)))
    assert find_description(zip_reader(archive), 'git', '5.2.0') is None


def test_extract_facts_for_a_plugin_uses_the_manifest_coordinates():
    data = make_hpi('io.jenkins.plugins', 'caffeine-api', '3.2.4')
    archive = open_archive(io.BytesIO(data))
    facts = extract_facts(
        zip_reader(archive),
        ('org.jenkins-ci.plugins', 'caffeine-api', '3.2.4'),
        plugin=True,
        released=archive_date(archive),
    )
    assert facts.coordinates == ('io.jenkins.plugins', 'caffeine-api', '3.2.4')
    assert facts.pom == pom_xml(
        'io.jenkins.plugins', 'caffeine-api', '3.2.4'
    )  # found under the real group
    assert facts.description == b'<div>An excellent plugin.</div>'
    assert b'Group-Id: io.jenkins.plugins' in facts.manifest
    assert facts.released == dt.datetime(2024, 5, 1, 12, 30, tzinfo=UTC)


def test_extract_facts_for_a_gradle_plugin_has_no_pom():
    archive = open_archive(io.BytesIO(make_hpi(*GIT, layout='gradle')))
    facts = extract_facts(zip_reader(archive), GIT, plugin=True, released=None)
    assert facts.pom is None
    assert facts.description == b'<div>An excellent plugin.</div>'


def test_extract_facts_for_a_war_uses_the_path():
    archive = open_archive(io.BytesIO(make_war('2.583')))
    facts = extract_facts(
        zip_reader(archive),
        ('org.jenkins-ci.main', 'jenkins-war', '2.583'),
        plugin=False,
        released=None,
    )
    assert facts.coordinates is None
    assert facts.pom == pom_xml(
        'org.jenkins-ci.main', 'jenkins-war', '2.583', 'Jenkins war'
    )
    assert facts.description is None
