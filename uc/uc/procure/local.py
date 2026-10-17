"""uc procure record [--fix]: record files placed in the staging folder by hand.

After the checks a download gets
(docs/how-to/procure-missing-files.md#files-you-place-by-hand).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

from uc.aql import WAR_ARTIFACT, WAR_GROUP, Gav, RepoFile, to_gav
from uc.doctor import required_jenkins_version
from uc.pom import pom_coordinates, unusable_reason
from uc.repo_adapter.archive import SMALL_ENTRY_LIMIT
from uc.settings import CORE_VERSION
from uc.timeutil import iso, utcnow

from . import ProcureError
from .dependencies import archive_manifest
from .staging import (
    EXTENSIONS,
    StagedFile,
    Staging,
    check_path,
    prune_empty,
    review_line,
    sha1_of,
)

if TYPE_CHECKING:
    from pathlib import Path

    from uc.pom import Coordinates

REASON = 'added by hand'


@dataclass(frozen=True)
class Declared:
    """The coordinates a file declares about itself.

    Also the Maven path they give it.
    """

    source: str  # its manifest, or its POM
    coordinates: Coordinates
    path: str

    def __str__(self) -> str:
        """Say where the file belongs, and on whose word."""
        return (
            f'{self.source} names {":".join(self.coordinates)}: '
            f'it belongs at {self.path}'
        )


def record(
    root: Path, source: str, *, fix: bool = False, out: TextIO = sys.stdout
) -> int:
    """Record every .hpi, .jpi, .war and .pom in root.

    Those procurement.json does not list yet, after the checks a download gets; with
    fix, first move each to the path it declares. Return 1 when any is refused.
    """
    staging = Staging.open(root, source)
    inside = root.resolve()
    recorded = refused = moved = 0
    for placed in staging.unrecorded():
        extension = placed.rsplit('.', 1)[-1]
        if extension not in EXTENSIONS:
            # notes, checksums and the like: push reports them, nothing to record
            continue
        path, file = placed, root / placed
        found = (
            declared(file, extension) if file.resolve().is_relative_to(inside) else None
        )
        misplaced = found is not None and found.path != placed
        if fix and found is not None and misplaced:
            try:
                _move(staging, root, inside, placed, found.path)
            except ProcureError as exc:
                refused += 1
                _say(out, 'REFUSED', placed, str(exc))
                continue
            moved += 1
            _say(out, 'moved', placed, f'to {found.path}')
            path, file, misplaced = found.path, root / found.path, False
        try:
            required_core = check_local(file, path, inside)
        except ProcureError as exc:
            refused += 1
            _say(
                out,
                'REFUSED',
                path,
                f'{exc}; --fix moves it there' if misplaced else str(exc),
            )
            continue
        staging.files[path] = StagedFile(
            path=path,
            url='',
            sha1=sha1_of(file),
            size=file.stat().st_size,
            kind='local',
            reason=REASON,
            staged_at=iso(utcnow()),
            required_core=required_core,
        )
        recorded += 1
        _say(out, 'recorded', path, REASON)
    if recorded:
        staging.save()
    done = f'{moved} moved, ' if fix else ''
    print(f'{done}{recorded} recorded, {refused} refused', file=out)
    if staging.files:
        print(review_line(root, len(staging.files)), file=out)
    return 1 if refused else 0


def check_local(file: Path, path: str, inside: Path) -> str | None:
    """Check a file placed by hand the way a download is checked.

    Return the core a plugin requires (None for a POM or core).
    """
    if not file.resolve().is_relative_to(inside):
        msg = 'not a file of its own inside the staging folder'
        raise ProcureError(msg)
    found = declared(file, path.rsplit('.', 1)[-1])
    try:
        check_path(path)
    except ProcureError as exc:
        if found is None:
            raise
        msg = f'not at a Maven path; {found}'
        raise ProcureError(msg) from exc
    if found is not None and found.path != path:
        raise ProcureError(str(found))
    directory, _, name = path.rpartition('/')
    gav = to_gav(RepoFile(directory, name))
    if gav is None:
        msg = 'not a file of its own inside the staging folder'
        raise ProcureError(msg)
    if gav.extension == 'pom':
        if file.stat().st_size > SMALL_ENTRY_LIMIT:
            msg = f'larger than {SMALL_ENTRY_LIMIT} bytes'
            raise ProcureError(msg)
        reason = unusable_reason(
            file.read_bytes(), (gav.group_id, gav.artifact_id, gav.version)
        )
        if reason is not None:
            msg = f'not a usable POM for its path: {reason}'
            raise ProcureError(msg)
        return None
    try:
        manifest = archive_manifest(file)
    except ProcureError as exc:
        msg = f'not a readable archive: {exc}'
        raise ProcureError(msg) from exc
    return None if gav.extension == 'war' else required_jenkins_version(manifest)


def declared(file: Path, extension: str) -> Declared | None:
    """Return the coordinates a file declares and the path they give it.

    The coordinates: a POM's own, a plugin manifest's, a core manifest's
    Jenkins-Version. None when it declares none.
    """
    try:
        if extension == 'pom':
            coordinates = (
                pom_coordinates(file.read_bytes())
                if file.stat().st_size <= SMALL_ENTRY_LIMIT
                else None
            )
            source = 'its POM'
        else:
            manifest = archive_manifest(file)
            source = 'its manifest'
            if extension == 'war':
                version = manifest.get('Jenkins-Version', '').strip()
                coordinates = (
                    (WAR_GROUP, WAR_ARTIFACT, version)
                    if CORE_VERSION.match(version)
                    else None
                )
            else:
                coordinates = (
                    manifest.get('Group-Id', ''),
                    manifest.get('Artifact-Id', ''),
                    manifest.get('Plugin-Version', ''),
                )
    except (OSError, ProcureError):
        return None
    if coordinates is None or not all(coordinates):
        return None
    target = Gav(*coordinates, extension).maven_path
    try:
        # the coordinates come from the file: only name a well-formed path
        check_path(target)
    except ProcureError:
        return None
    return Declared(source, coordinates, target)


def _move(staging: Staging, root: Path, inside: Path, path: str, target: str) -> None:
    """Move root/path to root/target.

    Creating its folders and removing those left empty; raise ProcureError, moving
    nothing, when target is taken or leads out of root.
    """
    source, destination = root / path, root / target
    problem = None
    if target in staging.files:
        problem = 'procurement.json already lists that path'
    elif destination.exists() or destination.is_symlink():
        same = destination.is_file() and sha1_of(destination) == sha1_of(source)
        problem = (
            'the same file is already there; delete this copy'
            if same
            else 'another file is already there'
        )
    else:
        existing = destination.parent
        while not existing.exists():
            existing = existing.parent
        # a link in the way would carry the file out
        if not existing.resolve().is_relative_to(inside):
            problem = 'that path leads out of the staging folder'
    if problem is None:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
        except OSError as exc:
            problem = str(exc)
    if problem is not None:
        msg = f'cannot move it to {target}: {problem}'
        raise ProcureError(msg)
    prune_empty(source.parent, root)


def _say(out: TextIO, label: str, path: str, detail: str) -> None:
    print(f'{label:<9} {path}  ({detail})', file=out)
