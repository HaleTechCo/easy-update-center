"""Releases on the data volume and the atomic live swap (spec §4, §6 step 10)."""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import re
import shutil
from pathlib import Path

from .errors import UcError

RELEASE_RE = re.compile(r"^\d{8}T\d{6}Z$")


class PublishError(UcError):
    pass


class LockBusy(UcError):
    pass


class RunLock:
    """Exclusive, non-blocking lock on DATA/.lock held for a whole generation."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / ".lock"
        self._fd: int | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockBusy(f"another generation holds {self.path}") from None
        self._fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._fd is not None
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None


def releases_dir(data_dir: Path) -> Path:
    return data_dir / "releases"


def new_release_id(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def staging_path(data_dir: Path, release_id: str) -> Path:
    return releases_dir(data_dir) / f"{release_id}.staging"


def create_staging(data_dir: Path, release_id: str) -> Path:
    path = staging_path(data_dir, release_id)
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def live_release(data_dir: Path) -> str | None:
    link = data_dir / "live"
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).name


def live_path(data_dir: Path) -> Path | None:
    release_id = live_release(data_dir)
    return releases_dir(data_dir) / release_id if release_id else None


def list_releases(data_dir: Path) -> list[str]:
    root = releases_dir(data_dir)
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and RELEASE_RE.match(p.name)
    )


def swap_live(data_dir: Path, release_id: str) -> None:
    if not (releases_dir(data_dir) / release_id).is_dir():
        raise PublishError(f"release {release_id} does not exist")
    tmp = data_dir / "live.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(f"releases/{release_id}", tmp)
    os.replace(tmp, data_dir / "live")  # rename(2): readers see the old or the new release, never neither


def publish(data_dir: Path, staging: Path, release_id: str) -> Path:
    final = releases_dir(data_dir) / release_id
    os.rename(staging, final)
    swap_live(data_dir, release_id)
    return final


def prune(data_dir: Path, retain: int) -> list[str]:
    releases = list_releases(data_dir)
    live = live_release(data_dir)
    keep = set(releases[-retain:]) | ({live} if live else set())
    removed = [r for r in releases if r not in keep]
    for release_id in removed:
        shutil.rmtree(releases_dir(data_dir) / release_id)
    return removed


def reclaim_orphans(data_dir: Path, current_id: str) -> list[str]:
    """Remove staging and work trees left by a run that was killed (spec §6 step 10).

    Called under the RunLock, so anything here belongs to a run that is no longer alive.
    `.failed-*` is deliberately kept: mark_failed preserves the last failure for debugging.
    """
    removed: list[str] = []
    root = releases_dir(data_dir)
    if root.is_dir():
        for path in root.glob("*.staging"):
            if path.name != f"{current_id}.staging":
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed.append(f"releases/{path.name}")
    work_root = data_dir / "work"
    if work_root.is_dir():
        for path in work_root.iterdir():
            if path.name != current_id:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed.append(f"work/{path.name}")
    return sorted(removed)


def mark_failed(data_dir: Path, release_id: str) -> None:
    """Keep the failed staging directory for debugging, replacing any older failure."""
    root = releases_dir(data_dir)
    if not root.is_dir():
        return
    for old in root.glob(".failed-*"):
        shutil.rmtree(old, ignore_errors=True)
    staging = staging_path(data_dir, release_id)
    if staging.exists():
        os.rename(staging, root / f".failed-{release_id}")


def rollback(data_dir: Path, target: str | None = None) -> str:
    releases = list_releases(data_dir)
    live = live_release(data_dir)
    if target is None:
        earlier = [r for r in releases if live is None or r < live]
        if not earlier:
            raise PublishError("no earlier release to roll back to")
        target = earlier[-1]
    elif target not in releases:
        raise PublishError(f"unknown release {target}; available: {', '.join(releases) or 'none'}")
    swap_live(data_dir, target)
    return target
