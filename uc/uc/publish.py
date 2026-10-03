"""Releases on the data volume and the atomic live swap (docs/architecture/generation-pipeline.md#step-10-publish)."""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
import shutil
from typing import TYPE_CHECKING, Self

from .errors import UcError

if TYPE_CHECKING:
    from pathlib import Path

RELEASE_RE = re.compile(r'^\d{8}T\d{6}Z$')


class PublishError(UcError):
    """A release could not be published or rolled back to."""


class LockBusyError(UcError):
    """Another generation or rollback holds the run lock."""


class RunLock:
    """Exclusive lock on DATA/.lock held for a whole generation or rollback."""

    def __init__(self, data_dir: Path, *, wait: bool = False) -> None:
        """Prepare the lock on data_dir/.lock; it is taken on entering the context, waiting for another holder when wait is true."""
        self.path = data_dir / '.lock'
        self.wait = wait
        self._fd: int | None = None

    def __enter__(self) -> Self:
        """Take the lock: wait for it, or raise LockBusyError at once if another process holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if self.wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            msg = f'another generation holds {self.path}'
            raise LockBusyError(msg) from None
        self._fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        """Release the lock."""
        if self._fd is None:
            msg = f'{self.path} is not held'
            raise RuntimeError(msg)
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def releases_dir(data_dir: Path) -> Path:
    """Return the directory holding every release."""
    return data_dir / 'releases'


def new_release_id(now: dt.datetime) -> str:
    """Return the release id for now: its UTC time as YYYYMMDDTHHMMSSZ."""
    return now.astimezone(dt.UTC).strftime('%Y%m%dT%H%M%SZ')


def staging_path(data_dir: Path, release_id: str) -> Path:
    """Return where release_id is built before it is published."""
    return releases_dir(data_dir) / f'{release_id}.staging'


def create_staging(data_dir: Path, release_id: str) -> Path:
    """Create an empty staging directory for release_id, replacing any leftover one, and return it."""
    path = staging_path(data_dir, release_id)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def live_release(data_dir: Path) -> str | None:
    """Return the id of the release the live symlink points at, or None when there is no live release."""
    link = data_dir / 'live'
    if not link.is_symlink():
        return None
    return link.readlink().name


def live_path(data_dir: Path) -> Path | None:
    """Return the live release's directory, or None when there is no live release."""
    release_id = live_release(data_dir)
    return releases_dir(data_dir) / release_id if release_id else None


def list_releases(data_dir: Path) -> list[str]:
    """Return the ids of the published releases, oldest first (staging and failed trees excluded)."""
    root = releases_dir(data_dir)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and RELEASE_RE.match(p.name))


def swap_live(data_dir: Path, release_id: str) -> None:
    """Point the live symlink at release_id atomically."""
    if not (releases_dir(data_dir) / release_id).is_dir():
        msg = f'release {release_id} does not exist'
        raise PublishError(msg)
    tmp = data_dir / 'live.tmp'
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(f'releases/{release_id}')
    tmp.replace(data_dir / 'live')  # rename(2): readers see the old or the new release, never neither


def publish(data_dir: Path, staging: Path, release_id: str) -> Path:
    """Move staging into place as release_id, make it live and return its directory."""
    final = releases_dir(data_dir) / release_id
    staging.rename(final)
    swap_live(data_dir, release_id)
    return final


def prune(data_dir: Path, retain: int) -> list[str]:
    """Delete all but the newest retain releases, always keeping the live one; return the ids removed."""
    releases = list_releases(data_dir)
    live = live_release(data_dir)
    keep = set(releases[-retain:]) | ({live} if live else set())
    removed = [r for r in releases if r not in keep]
    for release_id in removed:
        shutil.rmtree(releases_dir(data_dir) / release_id)
    return removed


def reclaim_orphans(data_dir: Path, current_id: str) -> list[str]:
    """Remove staging and work trees left by a run that was killed (docs/architecture/generation-pipeline.md#failed-runs).

    Called under the RunLock, so anything here belongs to a run that is no longer alive.
    `.failed-*` is deliberately kept: mark_failed preserves the last failure for debugging.
    """
    removed: list[str] = []
    root = releases_dir(data_dir)
    if root.is_dir():
        for path in root.glob('*.staging'):
            if path.name != f'{current_id}.staging':
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed.append(f'releases/{path.name}')
    work_root = data_dir / 'work'
    if work_root.is_dir():
        for path in work_root.iterdir():
            if path.name != current_id:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed.append(f'work/{path.name}')
    return sorted(removed)


def mark_failed(data_dir: Path, release_id: str) -> None:
    """Keep the failed staging directory for debugging, replacing any older failure."""
    root = releases_dir(data_dir)
    if not root.is_dir():
        return
    for old in root.glob('.failed-*'):
        shutil.rmtree(old, ignore_errors=True)
    staging = staging_path(data_dir, release_id)
    if staging.exists():
        staging.rename(root / f'.failed-{release_id}')


def rollback(data_dir: Path, target: str | None = None) -> str:
    """Make target live and return its id; by default the newest release before the live one (the newest of all when none is live)."""
    releases = list_releases(data_dir)
    live = live_release(data_dir)
    if target is None:
        earlier = [r for r in releases if live is None or r < live]
        if not earlier:
            msg = 'no earlier release to roll back to'
            raise PublishError(msg)
        target = earlier[-1]
    elif target not in releases:
        msg = f'unknown release {target}; available: {", ".join(releases) or "none"}'
        raise PublishError(msg)
    swap_live(data_dir, target)
    return target
