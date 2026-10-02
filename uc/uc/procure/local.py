"""uc doctor --stage-local: record files placed in the staging folder by hand, after the checks a download gets (docs/how-to/procure-missing-files.md)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, TextIO

from uc.aql import WAR_ARTIFACT, WAR_GROUP, Gav, RepoFile, to_gav
from uc.doctor import CORE_VERSION, required_jenkins_version
from uc.pom import pom_coordinates, unusable_reason
from uc.repo_adapter.archive import SMALL_ENTRY_LIMIT
from uc.timeutil import iso, utcnow

from . import ProcureError
from .dependencies import archive_manifest
from .staging import EXTENSIONS, StagedFile, Staging, check_path, sha1_of

if TYPE_CHECKING:
    from pathlib import Path

REASON = 'added by hand'


def stage_local(root: Path, source: str, *, out: TextIO = sys.stdout) -> int:
    """Record every .hpi, .jpi, .war and .pom in root that procurement.json does not list yet, after the checks a download gets; return 1 when any is refused."""
    staging = Staging.open(root, source)
    inside = root.resolve()
    recorded = refused = 0
    for path in staging.unrecorded():
        if path.rsplit('.', 1)[-1] not in EXTENSIONS:
            continue  # notes, checksums and the like: push reports them, nothing to record
        file = root / path
        try:
            required_core = check_local(file, path, inside)
        except ProcureError as exc:
            refused += 1
            _say(out, 'REFUSED', path, str(exc))
            continue
        staging.files[path] = StagedFile(path=path, url='', sha1=sha1_of(file), size=file.stat().st_size, kind='local', reason=REASON, staged_at=iso(utcnow()), required_core=required_core)
        recorded += 1
        _say(out, 'recorded', path, REASON)
    if recorded:
        staging.save()
    print(f'{recorded} recorded, {refused} refused; review {root}, then run uc doctor --push', file=out)
    return 1 if refused else 0


def check_local(file: Path, path: str, inside: Path) -> str | None:
    """Check a file placed by hand the way a download is checked; return the core a plugin requires (None for a POM or core)."""
    try:
        check_path(path)
    except ProcureError as exc:
        belongs = belongs_at(file, path.rsplit('.', 1)[-1]) if file.resolve().is_relative_to(inside) else None
        if belongs is None:
            raise
        msg = f'not at a Maven path; {belongs}'
        raise ProcureError(msg) from exc
    directory, _, name = path.rpartition('/')
    gav = to_gav(RepoFile(directory, name))
    if gav is None or not file.resolve().is_relative_to(inside):
        msg = 'not a file of its own inside the staging folder'
        raise ProcureError(msg)
    if gav.extension == 'pom':
        if file.stat().st_size > SMALL_ENTRY_LIMIT:
            msg = f'larger than {SMALL_ENTRY_LIMIT} bytes'
            raise ProcureError(msg)
        reason = unusable_reason(file.read_bytes(), (gav.group_id, gav.artifact_id, gav.version))
        if reason is not None:
            msg = f'not a usable POM for its path: {reason}'
            raise ProcureError(msg)
        return None
    try:
        manifest = archive_manifest(file)
    except ProcureError as exc:
        msg = f'not a readable archive: {exc}'
        raise ProcureError(msg) from exc
    if gav.extension == 'war':
        return None
    declared = (manifest.get('Group-Id', ''), manifest.get('Artifact-Id', ''), manifest.get('Plugin-Version', ''))
    if all(declared) and declared != (gav.group_id, gav.artifact_id, gav.version):
        msg = f'its manifest names {":".join(declared)}: it belongs at {Gav(*declared, gav.extension).maven_path}'
        raise ProcureError(msg)
    return required_jenkins_version(manifest)


def belongs_at(file: Path, extension: str) -> str | None:
    """Say which Maven path a file belongs at, from the coordinates its manifest or POM declares; None when it declares none."""
    try:
        if extension == 'pom':
            coordinates = pom_coordinates(file.read_bytes()) if file.stat().st_size <= SMALL_ENTRY_LIMIT else None
            source = 'its POM'
        else:
            manifest = archive_manifest(file)
            if extension == 'war':
                version = manifest.get('Jenkins-Version', '').strip()
                coordinates = (WAR_GROUP, WAR_ARTIFACT, version) if CORE_VERSION.match(version) else None
            else:
                coordinates = (manifest.get('Group-Id', ''), manifest.get('Artifact-Id', ''), manifest.get('Plugin-Version', ''))
            source = 'its manifest'
    except (OSError, ProcureError):
        return None
    if coordinates is None or not all(coordinates):
        return None
    target = Gav(*coordinates, extension).maven_path
    try:
        check_path(target)  # the coordinates come from the file: only name a well-formed path
    except ProcureError:
        return None
    return f'{source} names {":".join(coordinates)}: it belongs at {target}'


def _say(out: TextIO, label: str, path: str, detail: str) -> None:
    print(f'{label:<9} {path}  ({detail})', file=out)
