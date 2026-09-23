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
    def __init__(self, files: dict[str, bytes], *, type: str = 'artifact-keeper', uploaded: str = UPLOADED) -> None:
        self.type = type
        self.files = dict(files)
        self.uploaded = uploaded
        self.failing: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    def list(self) -> list[RepoEntry]:
        self.calls.append(('list', ''))
        entries = []
        for path, data in sorted(self.files.items()):
            directory, _, name = path.rpartition('/')
            sha1 = hashlib.sha1(data).hexdigest() if self.type == 'artifactory' else None
            entries.append(RepoEntry(directory, name, hashlib.sha256(data).hexdigest(), sha1, len(data), self.uploaded, self.uploaded))
        return entries

    def get(self, path: str) -> bytes | None:
        self.calls.append(('get', path))
        return self.files.get(path)

    def sha1(self, entry: RepoEntry) -> str:
        self.calls.append(('sha1', entry.path))
        return entry.sha1 or hashlib.sha1(self.files[entry.path]).hexdigest()

    def facts(self, entry: RepoEntry, path_coordinates, *, plugin: bool):
        self.calls.append(('facts', entry.path))
        if entry.path in self.failing:
            raise BackendError(f'GET {entry.path} failed: connection reset')
        archive = open_archive(io.BytesIO(self.files[entry.path]))
        return extract_facts(zip_reader(archive), path_coordinates, plugin=plugin, released=archive_date(archive))

    def jar_description(self, jar_path: str) -> bytes | None:
        self.calls.append(('jar', jar_path))
        data = self.files.get(jar_path)
        if data is None:
            return None
        try:
            return jar_description(data)
        except ArchiveError:
            return None
