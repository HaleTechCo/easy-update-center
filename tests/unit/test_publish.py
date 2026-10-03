"""Unit tests for uc.publish: staging, the atomic live swap, pruning, rollback and the run lock."""

import datetime as dt
import threading
from pathlib import Path

import pytest

from uc.publish import (
    LockBusyError,
    PublishError,
    RunLock,
    create_staging,
    list_releases,
    live_path,
    live_release,
    mark_failed,
    new_release_id,
    prune,
    publish,
    reclaim_orphans,
    releases_dir,
    rollback,
    swap_live,
)

R1, R2, R3, R4 = '20260922T010000Z', '20260922T020000Z', '20260922T030000Z', '20260922T040000Z'


def make_release(data, rid):
    path = releases_dir(data) / rid
    path.mkdir(parents=True)
    (path / 'marker').write_text(rid)
    return path


def test_release_id():
    assert new_release_id(dt.datetime(2026, 9, 22, 3, 4, 5, tzinfo=dt.UTC)) == '20260922T030405Z'


def test_publish_swaps_live_atomically(tmp_path):
    staging = create_staging(tmp_path, R1)
    (staging / 'marker').write_text('first')
    publish(tmp_path, staging, R1)
    assert (tmp_path / 'live').readlink() == Path(f'releases/{R1}')
    assert (tmp_path / 'live' / 'marker').read_text() == 'first'
    assert live_release(tmp_path) == R1
    assert live_path(tmp_path) == releases_dir(tmp_path) / R1
    assert not staging.exists()

    second = create_staging(tmp_path, R2)
    (second / 'marker').write_text('second')
    publish(tmp_path, second, R2)
    assert (tmp_path / 'live' / 'marker').read_text() == 'second'
    assert not (tmp_path / 'live.tmp').exists()


def test_create_staging_replaces_leftovers(tmp_path):
    (create_staging(tmp_path, R1) / 'leftover').write_text('x')
    assert not (create_staging(tmp_path, R1) / 'leftover').exists()


def test_list_releases_ignores_staging_failed_and_junk(tmp_path):
    make_release(tmp_path, R2)
    make_release(tmp_path, R1)
    create_staging(tmp_path, R3)
    (releases_dir(tmp_path) / f'.failed-{R4}').mkdir()
    (releases_dir(tmp_path) / 'junk').mkdir()
    assert list_releases(tmp_path) == [R1, R2]


def test_prune_keeps_newest_and_live(tmp_path):
    for rid in (R1, R2, R3, R4):
        make_release(tmp_path, rid)
    swap_live(tmp_path, R1)  # e.g. after a rollback
    assert prune(tmp_path, 2) == [R2]
    assert list_releases(tmp_path) == [R1, R3, R4]


def test_mark_failed_keeps_only_the_latest_failure(tmp_path):
    create_staging(tmp_path, R1)
    mark_failed(tmp_path, R1)
    create_staging(tmp_path, R2)
    mark_failed(tmp_path, R2)
    assert sorted(p.name for p in releases_dir(tmp_path).glob('.failed-*')) == [f'.failed-{R2}']


def test_reclaim_orphans_removes_stale_staging_and_work(tmp_path):
    create_staging(tmp_path, R1)  # belongs to the current run
    create_staging(tmp_path, R2)  # left by a killed run
    (tmp_path / 'work' / R1).mkdir(parents=True)
    (tmp_path / 'work' / R2).mkdir(parents=True)
    (releases_dir(tmp_path) / f'.failed-{R3}').mkdir(parents=True)
    make_release(tmp_path, R4)

    assert reclaim_orphans(tmp_path, R1) == [f'releases/{R2}.staging', f'work/{R2}']

    assert (releases_dir(tmp_path) / f'{R1}.staging').is_dir()
    assert (tmp_path / 'work' / R1).is_dir()
    assert not (releases_dir(tmp_path) / f'{R2}.staging').exists()
    assert not (tmp_path / 'work' / R2).exists()
    assert (releases_dir(tmp_path) / f'.failed-{R3}').is_dir()
    assert (releases_dir(tmp_path) / R4).is_dir()


def test_reclaim_orphans_tolerates_missing_directories(tmp_path):
    assert reclaim_orphans(tmp_path, R1) == []


def test_rollback(tmp_path):
    for rid in (R1, R2, R3):
        make_release(tmp_path, rid)
    swap_live(tmp_path, R3)
    assert rollback(tmp_path) == R2
    assert live_release(tmp_path) == R2
    assert rollback(tmp_path, R3) == R3
    with pytest.raises(PublishError, match='unknown release'):
        rollback(tmp_path, 'nope')
    swap_live(tmp_path, R1)
    with pytest.raises(PublishError, match='no earlier release'):
        rollback(tmp_path)


def test_swap_to_missing_release(tmp_path):
    with pytest.raises(PublishError, match='does not exist'):
        swap_live(tmp_path, R1)


def test_run_lock_is_exclusive(tmp_path):
    with RunLock(tmp_path), pytest.raises(LockBusyError, match='another generation'), RunLock(tmp_path):
        pass
    with RunLock(tmp_path):
        pass


def test_run_lock_can_wait_for_the_holder(tmp_path):
    entered = threading.Event()

    def waiter():
        with RunLock(tmp_path, wait=True):
            entered.set()

    with RunLock(tmp_path):
        thread = threading.Thread(target=waiter)
        thread.start()
        assert not entered.wait(0.3)
    thread.join(5)
    assert entered.is_set()
