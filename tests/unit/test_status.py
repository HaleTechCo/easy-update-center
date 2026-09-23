"""Unit tests for uc.status: reading, writing and updating status.json."""

from uc.status import read_status, update_status, write_status


def test_status_roundtrip(tmp_path):
    assert read_status(tmp_path) == {}
    write_status(tmp_path, {'live_release': 'a'})
    assert read_status(tmp_path) == {'live_release': 'a'}
    assert (tmp_path / 'status.json').stat().st_mode & 0o777 == 0o644
    assert update_status(tmp_path, plugin_count=3) == {'live_release': 'a', 'plugin_count': 3}
    assert read_status(tmp_path)['plugin_count'] == 3


def test_corrupt_status_reads_as_empty(tmp_path):
    (tmp_path / 'status.json').write_text('{not json')
    assert read_status(tmp_path) == {}
