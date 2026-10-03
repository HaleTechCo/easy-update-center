"""Unit tests for uc.status: reading, writing and updating status.json."""

import threading

from uc.status import locked, read_status, update_status, write_status


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


def test_writers_wait_for_each_other(tmp_path):
    write_status(tmp_path, {'live_release': 'a'})
    done = threading.Event()

    def writer():
        update_status(tmp_path, doctor={'plugins': 1})
        done.set()

    with locked(tmp_path):
        thread = threading.Thread(target=writer)
        thread.start()
        assert not done.wait(0.3)
    thread.join(5)
    assert done.is_set()
    assert read_status(tmp_path) == {'live_release': 'a', 'doctor': {'plugins': 1}}


def test_keep_holds_a_field_the_writer_has_no_value_for(tmp_path):
    write_status(tmp_path, {'doctor': {'plugins': 1}, 'old': True})
    write_status(tmp_path, {'live_release': 'b', 'doctor': None}, keep=('doctor',))
    assert read_status(tmp_path) == {'live_release': 'b', 'doctor': {'plugins': 1}}
    write_status(tmp_path, {'doctor': {'plugins': 2}}, keep=('doctor',))
    assert read_status(tmp_path) == {'doctor': {'plugins': 2}}


def test_keep_holds_a_field_of_a_block_the_writer_has_no_value_for(tmp_path):
    keep = ('doctor', 'doctor.missing_cores', 'doctor.recorded_at')
    write_status(tmp_path, {'doctor': {'plugins': 1, 'missing_cores': 2, 'recorded_at': 'r'}})
    write_status(tmp_path, {'doctor': {'plugins': 3, 'missing_cores': None}}, keep=keep)
    assert read_status(tmp_path) == {'doctor': {'plugins': 3, 'missing_cores': 2, 'recorded_at': 'r'}}
    write_status(tmp_path, {'doctor': {'plugins': 4, 'missing_cores': 5}}, keep=keep)
    assert read_status(tmp_path) == {'doctor': {'plugins': 4, 'missing_cores': 5, 'recorded_at': 'r'}}
    write_status(tmp_path, {'doctor': None}, keep=keep)
    assert read_status(tmp_path) == {'doctor': {'plugins': 4, 'missing_cores': 5, 'recorded_at': 'r'}}
    write_status(tmp_path, {})
    write_status(tmp_path, {'doctor': {'plugins': 6, 'missing_cores': None}}, keep=keep)
    assert read_status(tmp_path) == {'doctor': {'plugins': 6, 'missing_cores': None}}
