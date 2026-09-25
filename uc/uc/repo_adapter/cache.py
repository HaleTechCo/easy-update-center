"""Content-addressed cache of small derived files, keyed by sha256 (docs/architecture/repo-adapter.md#cache)."""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from .archive import ArchiveFacts

_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_SHARD_LENGTH = 2  # <root>/<sha256[:2]>/
PRUNE_AFTER_DAYS = 30


class Cache:
    """<root>/<sha256[:2]>/<sha256>/ holds whatever is known about that content.

    An archive's facts are complete once `released` or `released.none` exists: it is written last.
    """

    def __init__(self, root: Path) -> None:
        """Keep the cache under root, which the first write creates."""
        self.root = root

    def _dir(self, sha256: str) -> Path:
        if not _SHA256.match(sha256):
            msg = f'not a sha256: {sha256!r}'
            raise ValueError(msg)
        return self.root / sha256[:_SHARD_LENGTH] / sha256

    def _read(self, sha256: str, name: str) -> bytes | None:
        try:
            return (self._dir(sha256) / name).read_bytes()
        except FileNotFoundError:
            return None

    def _write(self, sha256: str, name: str, data: bytes) -> None:
        directory = self._dir(sha256)
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.tmp-')
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
        Path(tmp).replace(directory / name)

    def touch(self, sha256: str) -> None:
        """Mark the content's entry as used now, so prune keeps it."""
        directory = self._dir(sha256)
        if directory.is_dir():
            os.utime(directory)

    def _optional(self, sha256: str, name: str, marker: str, data: bytes | None) -> None:
        """Write data as name, or the empty marker file when it is None: "known absent"."""
        if data is None:
            self._write(sha256, marker, b'')
        else:
            self._write(sha256, name, data)

    def store_facts(self, sha256: str, facts: ArchiveFacts) -> None:
        """Cache an archive's facts, `released` last so an interrupted write never reads as complete."""
        coordinates = ':'.join(facts.coordinates).encode() if facts.coordinates else None
        self._optional(sha256, 'MANIFEST.MF', 'MANIFEST.none', facts.manifest)
        self._optional(sha256, 'coordinates', 'coordinates.none', coordinates)
        self._optional(sha256, 'pom.xml', 'pom.none', facts.pom)
        self._optional(sha256, 'index.jelly', 'index.none', facts.description)
        released = facts.released.isoformat().encode() if facts.released else None
        self._optional(sha256, 'released', 'released.none', released)  # last: marks the entry complete

    def facts(self, sha256: str) -> ArchiveFacts | None:
        """Return an archive's cached facts, None unless they are complete; reading them marks the entry used."""
        directory = self._dir(sha256)
        if not (directory / 'released').is_file() and not (directory / 'released.none').is_file():
            return None
        coordinates = self._read(sha256, 'coordinates')
        released = self._read(sha256, 'released')
        self.touch(sha256)
        group, artifact, version = coordinates.decode().split(':', 2) if coordinates else (None, None, None)
        return ArchiveFacts(
            manifest=self._read(sha256, 'MANIFEST.MF'),
            coordinates=(group, artifact, version) if coordinates else None,
            pom=self._read(sha256, 'pom.xml'),
            description=self._read(sha256, 'index.jelly'),
            released=dt.datetime.fromisoformat(released.decode()) if released else None,
        )

    def sha1(self, sha256: str) -> str | None:
        """Return the content's cached sha1, None when not cached."""
        data = self._read(sha256, 'sha1')
        return data.decode() if data else None

    def store_sha1(self, sha256: str, sha1: str) -> None:
        """Cache the content's sha1."""
        self._write(sha256, 'sha1', sha1.encode())

    def pom_verdict(self, sha256: str) -> str | None:
        """'' for a usable POM, a reason for an unusable one, None when not yet checked."""
        data = self._read(sha256, 'pom-verdict')
        return data.decode() if data is not None else None

    def store_pom_verdict(self, sha256: str, reason: str | None) -> None:
        """Cache why a repository POM is unusable; reason None records a usable one."""
        self._write(sha256, 'pom-verdict', (reason or '').encode())

    def description(self, sha256: str) -> bytes | None:
        """index.jelly extracted from a repository .jar (rule 6), when cached."""
        return self._read(sha256, 'jar-index.jelly')

    def has_description(self, sha256: str) -> bool:
        """Return whether a repository .jar's index.jelly, or its absence, is cached."""
        directory = self._dir(sha256)
        return (directory / 'jar-index.jelly').is_file() or (directory / 'jar-index.none').is_file()

    def store_description(self, sha256: str, data: bytes | None) -> None:
        """Cache a repository .jar's index.jelly, or that it has none when data is None."""
        self._optional(sha256, 'jar-index.jelly', 'jar-index.none', data)

    def prune(self, now: float | None = None, max_age_days: int = PRUNE_AFTER_DAYS) -> int:
        """Remove content directories untouched for max_age_days; returns how many."""
        cutoff = (now if now is not None else time.time()) - max_age_days * 86400
        removed = 0
        if not self.root.is_dir():
            return 0
        for shard in self.root.iterdir():
            if not shard.is_dir() or len(shard.name) != _SHARD_LENGTH:
                continue
            for entry in shard.iterdir():
                if entry.is_dir() and _SHA256.match(entry.name) and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
        return removed
