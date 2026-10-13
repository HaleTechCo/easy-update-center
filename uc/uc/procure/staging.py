"""The staging folder: files in Maven layout plus procurement.json.

procurement.json is the record reviewers approve and push uploads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from uc.aql import RepoFile, to_gav

from . import ProcureError

MANIFEST = 'procurement.json'
FORMAT = 1
KINDS = ('pom', 'dependency', 'core', 'local')
EXTENSIONS = ('hpi', 'jpi', 'war', 'pom')
SHA1 = re.compile(r'^[0-9a-f]{40}$')
DEFAULT_STAGING = Path('/procurement')
"""PROCUREMENT_STAGING_DIR's default.

Where compose.yaml mounts ./procurement in the uc container.
"""


def shown_staging(staging_dir: Path) -> str:
    """Return how a next: line names the staging folder.

    ./procurement, where it is on the host, for the default.
    """
    return './procurement' if staging_dir == DEFAULT_STAGING else str(staging_dir)


def review_line(staging_dir: Path, listed: int) -> str:
    """Return the next: line after staging or recording.

    Review the folder, then push.
    """
    files = '1 file' if listed == 1 else f'{listed} files'
    return (
        f'next: review {shown_staging(staging_dir)} '
        f'(procurement.json lists {files}), then uc procure push'
    )


@dataclass(frozen=True)
class StagedFile:
    """One staged file as procurement.json records it."""

    path: str
    url: str
    sha1: str
    size: int
    # pom, dependency, core, or local (placed by hand, recorded by uc procure record)
    kind: str
    reason: str
    staged_at: str
    required_core: str | None = None  # plugin archives: the manifest's Jenkins-Version
    # the repository holds an unusable file at this path
    replaces_unusable: bool = False


def check_path(path: str) -> str:
    """Return path when it is a relative Maven path to a .hpi, .jpi, .war or .pom.

    Raise ProcureError otherwise.
    """
    directory, _, name = path.rpartition('/')
    gav = to_gav(RepoFile(directory, name)) if directory else None
    if (
        path.startswith('/')
        or '\\' in path
        or any(part in {'', '.', '..'} for part in path.split('/'))
        or gav is None
        or gav.extension not in EXTENSIONS
    ):
        msg = f'not a Maven path to a .hpi, .jpi, .war or .pom: {path!r}'
        raise ProcureError(msg)
    return path


def in_layout(path: str) -> bool:
    """Return whether path is a relative Maven path to a .hpi, .jpi, .war or .pom."""
    try:
        check_path(path)
    except ProcureError:
        return False
    return True


def prune_empty(folder: Path, root: Path) -> None:
    """Remove folder, then its parents, while they are empty.

    Up to but not including root.
    """
    while folder != root and folder.is_relative_to(root):
        try:
            folder.rmdir()
        except OSError:  # not empty, or already gone
            return
        folder = folder.parent


def sha1_of(file: Path) -> str:
    """Return the file's sha1 as 40 lower-case hex digits."""
    digest = hashlib.sha1(usedforsecurity=False)
    with file.open('rb') as data:
        while chunk := data.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _refuse(manifest: Path, detail: object) -> ProcureError:
    msg = (
        f'{manifest} is not a procurement.json this version of uc wrote ({detail}); '
        'move it away and download again'
    )
    return ProcureError(msg)


class Staging:
    """A staging folder and its procurement.json.

    procurement.json changes only on save().
    """

    def __init__(self, root: Path, source: str, files: dict[str, StagedFile]) -> None:
        """Use root, recording source as where downloads come from.

        With files already staged.
        """
        self.root, self.source, self.files = root, source, files

    @classmethod
    def open(cls, root: Path, source: str) -> Staging:
        """Read root/procurement.json when there is one.

        Raise ProcureError when it is not one this tool wrote.
        """
        manifest = root / MANIFEST
        if not manifest.is_file():
            return cls(root, source, {})
        try:
            data = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise _refuse(manifest, exc) from exc
        entries = (
            data.get('files')
            if isinstance(data, dict) and data.get('format') == FORMAT
            else None
        )
        if not isinstance(entries, dict):
            raise _refuse(manifest, f'expected format {FORMAT} with a files object')
        try:
            files = {
                path: StagedFile(path=path, **entry) for path, entry in entries.items()
            }
        except TypeError as exc:
            raise _refuse(manifest, exc) from exc
        for path, staged in files.items():
            check_path(path)
            if staged.kind not in KINDS or not SHA1.match(str(staged.sha1)):
                msg = (
                    f'{manifest}: {path} has kind {staged.kind!r} '
                    f'and sha1 {staged.sha1!r}'
                )
                raise ProcureError(msg)
        return cls(root, source, files)

    def file(self, path: str) -> Path:
        """Return where path lives in the folder.

        ProcureError when a folder on its way is a link out of the staging folder: a
        download or a delete must never land outside it.
        """
        target = self.root / check_path(path)
        if not target.parent.resolve().is_relative_to(self.root.resolve()):
            msg = (
                f'{path}: a folder on its way is a link out of {self.root}; '
                'remove the link'
            )
            raise ProcureError(msg)
        return target

    def intact(self, path: str) -> bool:
        """Return whether path is recorded and its file is present.

        With the recorded sha1.
        """
        staged = self.files.get(path)
        file = self.root / path
        return staged is not None and file.is_file() and sha1_of(file) == staged.sha1

    def temporary(self) -> Path:
        """Return a new empty file in the folder to download into.

        commit() moves it into place.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.root, prefix='.part-')
        except OSError as exc:
            msg = (
                f'cannot write to {self.root}: {exc}. Check that UC_UID and UC_GID '
                'in .env are the owner of ./procurement (id -u, id -g)'
            )
            raise ProcureError(msg) from exc
        os.close(fd)
        return Path(name)

    def commit(self, staged: StagedFile, part: Path) -> None:
        """Move a downloaded file into place, readable by everyone, and record it."""
        target = self.file(staged.path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # readable by everyone, as a downloaded file would be;
            # mkstemp creates 0600
            part.chmod(0o644)
            part.replace(target)
        except OSError as exc:
            msg = f'cannot write {target}: {exc}'
            raise ProcureError(msg) from exc
        self.files[staged.path] = staged

    def unrecorded(self) -> list[str]:
        """Return the folder's files that procurement.json does not list.

        In path order: files placed by hand, or anything else.

        procurement.json itself and unfinished downloads (.part-*) are left out.
        """
        if not self.root.is_dir():
            return []
        found = []
        for file in self.root.rglob('*'):
            path = file.relative_to(self.root).as_posix()
            if (
                file.is_file()
                and path != MANIFEST
                and not file.name.startswith('.part-')
                and path not in self.files
            ):
                found.append(path)
        return sorted(found)

    def remove(self, path: str) -> None:
        """Delete path's file and forget it.

        When the delete fails (OSError), path stays recorded.
        """
        self.file(path).unlink(missing_ok=True)
        self.files.pop(path, None)

    def save(self) -> None:
        """Write procurement.json atomically, files in path order."""
        body = {
            'format': FORMAT,
            'source': self.source,
            'files': {
                path: {k: v for k, v in asdict(staged).items() if k != 'path'}
                for path, staged in sorted(self.files.items())
            },
        }
        part = self.temporary()
        part.write_text(json.dumps(body, indent=2) + '\n', encoding='utf-8')
        # readable by everyone, as a downloaded file would be; mkstemp creates 0600
        part.chmod(0o644)
        part.replace(self.root / MANIFEST)
