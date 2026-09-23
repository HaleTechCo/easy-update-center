"""An in-memory repository with the Backend interface, for listing and server tests."""

from __future__ import annotations

import hashlib
import io

from uc.repo_adapter.archive import (
    ArchiveError,
    archive_date,
    extract_facts,
    jar_description,
    open_archive,
    zip_reader,
)
from uc.repo_adapter.backends import BackendError, RepoEntry

UPLOADED = '2026-09-22T23:09:48.946Z'


class FakeBackend:
    """Serves files (repository path to bytes) and records every call; paths in `failing` raise on facts()."""

    def __init__(self, files: dict[str, bytes], *, backend_type: str = 'artifact-keeper', uploaded: str = UPLOADED) -> None:
        """Hold a copy of files; backend_type 'artifactory' also lists sha1s, and uploaded dates every entry."""
        self.type = backend_type
        self.files = dict(files)
        self.uploaded = uploaded
        self.failing: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def list(self) -> list[RepoEntry]:
        """Return an entry per file, sorted by path."""
        self.calls.append(('list', ''))
        entries = []
        for path, data in sorted(self.files.items()):
            directory, _, name = path.rpartition('/')
            sha1 = hashlib.sha1(data, usedforsecurity=False).hexdigest() if self.type == 'artifactory' else None
            entries.append(RepoEntry(directory, name, hashlib.sha256(data).hexdigest(), sha1, len(data), self.uploaded, self.uploaded))
        return entries

    def get(self, path: str) -> bytes | None:
        """Return the file's bytes, or None when there is no such path."""
        self.calls.append(('get', path))
        return self.files.get(path)

    def sha1(self, entry: RepoEntry) -> str:
        """Return the listed sha1, or compute it from the file."""
        self.calls.append(('sha1', entry.path))
        return entry.sha1 or hashlib.sha1(self.files[entry.path], usedforsecurity=False).hexdigest()

    def facts(self, entry: RepoEntry, path_coordinates, *, plugin: bool):
        """Extract the archive's facts as the real backends do; raise BackendError for paths in `failing`."""
        self.calls.append(('facts', entry.path))
        if entry.path in self.failing:
            msg = f'GET {entry.path} failed: connection reset'
            raise BackendError(msg)
        archive = open_archive(io.BytesIO(self.files[entry.path]))
        return extract_facts(zip_reader(archive), path_coordinates, plugin=plugin, released=archive_date(archive))

    def jar_description(self, jar_path: str) -> bytes | None:
        """Return the jar's index.jelly, or None when the jar or the entry is missing or unreadable."""
        self.calls.append(('jar', jar_path))
        data = self.files.get(jar_path)
        if data is None:
            return None
        try:
            return jar_description(data)
        except ArchiveError:
            return None
