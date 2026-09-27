"""The gap-filled listing the adapter serves, and its report (docs/architecture/repo-adapter.md#filling-gaps)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from uc.aql import Gav, RepoFile, to_gav
from uc.config import PUBLIC_RELEASES_URL
from uc.pom import Coordinates, unusable_reason
from uc.timeutil import iso

from .archive import ArchiveError, ArchiveFacts, plausible
from .backends import Backend, BackendError, RepoEntry, artifactory_time, parse_time

if TYPE_CHECKING:
    import datetime as dt

    from .cache import Cache

ARCHIVE_EXTENSIONS = ('hpi', 'jpi', 'war')
REPORT_LISTS = ('derived_poms', 'replaced_poms', 'needs_pom', 'misplaced', 'skipped', 'dates_from_repository')


@dataclass
class Report:
    """What one listing filled in, replaced or left out, for operators and uc doctor (docs/architecture/repo-adapter.md#report)."""

    generated_at: str
    repo: dict
    archives: int = 0
    derived_poms: list[dict] = field(default_factory=list)
    replaced_poms: list[dict] = field(default_factory=list)
    needs_pom: list[dict] = field(default_factory=list)
    misplaced: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    dates_from_repository: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Return the length of each of the report's lists."""
        return {name: len(getattr(self, name)) for name in REPORT_LISTS}


@dataclass
class Index:
    """One listing: the AQL rows, and what GETs resolve against until the next listing."""

    rows: list[dict] = field(default_factory=list)
    poms: dict[str, bytes | None] = field(default_factory=dict)  # path -> derived bytes, None: the repository's
    manifests: dict[str, bytes] = field(default_factory=dict)  # archive path -> MANIFEST.MF
    descriptions: dict[Coordinates, bytes] = field(default_factory=dict)  # plugin -> index.jelly from its .hpi
    jars: dict[str, RepoEntry] = field(default_factory=dict)  # the repository's .jar files (Artifact Keeper)
    plugins: set[Coordinates] = field(default_factory=set)  # every plugin archive in the rows
    report: Report | None = None


def _reason(exc: Exception) -> str:
    """Our errors carry their own message; anything else also names its type."""
    return str(exc) if isinstance(exc, (BackendError, ArchiveError)) else f'{type(exc).__name__}: {exc}'


def _row(key: str, directory: str, name: str, *, sha256: str, sha1: str, size: int, created: str, modified: str) -> dict:
    return {'repo': key, 'path': directory, 'name': name, 'created': created, 'modified': modified, 'sha256': sha256, 'actual_sha1': sha1, 'size': size}


def _facts(backend: Backend, cache: Cache, entry: RepoEntry, coordinates: Coordinates, *, plugin: bool) -> ArchiveFacts:
    facts = cache.facts(entry.sha256)
    if facts is None:
        facts = backend.facts(entry, coordinates, plugin=plugin)
        cache.store_facts(entry.sha256, facts)
    return facts


def _sha1(backend: Backend, cache: Cache, entry: RepoEntry) -> str:
    if entry.sha1:
        return entry.sha1
    cached = cache.sha1(entry.sha256)
    if cached is None:
        cached = backend.sha1(entry)
        cache.store_sha1(entry.sha256, cached)
    return cached


def _pom_verdict(backend: Backend, cache: Cache, entry: RepoEntry, coordinates: Coordinates) -> str | None:
    """None for a usable repository POM, otherwise the reason it is not (rule 3)."""
    cached = cache.pom_verdict(entry.sha256)
    if cached is not None:
        return cached or None
    data = backend.get(entry.path)
    reason = 'missing from the repository' if data is None else unusable_reason(data, coordinates)
    cache.store_pom_verdict(entry.sha256, reason)
    return reason


def build_index(backend: Backend, cache: Cache, *, key: str, now: dt.datetime) -> Index:
    """List the repository and fill its gaps: archive dates, POMs derived from the archives, and the report."""
    entries = backend.list()
    report = Report(generated_at=iso(now), repo={'type': backend.type, 'key': key})
    index = Index(report=report)
    poms = {e.path: e for e in entries if e.extension == 'pom'}
    index.jars = {e.path: e for e in entries if e.extension == 'jar'}
    consumed: set[str] = set()

    for entry in sorted((e for e in entries if e.extension in ARCHIVE_EXTENSIONS), key=lambda e: e.path):
        report.archives += 1
        gav = to_gav(RepoFile(entry.directory, entry.name))
        if gav is None:  # not in Maven layout: upstream rejects it, and uc doctor reports it
            index.rows.append(_row(key, entry.directory, entry.name, sha256=entry.sha256, sha1=entry.sha1 or '', size=entry.size, created=entry.created, modified=entry.modified))
            continue
        pom_path = gav.with_extension('pom').maven_path
        consumed.add(pom_path)
        coordinates = (gav.group_id, gav.artifact_id, gav.version)
        plugin = entry.extension != 'war'
        try:
            facts = _facts(backend, cache, entry, coordinates, plugin=plugin)
            sha1 = _sha1(backend, cache, entry)
        except Exception as exc:  # noqa: BLE001 - one bad archive must not stop the listing: it is dropped and reported
            report.skipped.append({'path': entry.path, 'reason': _reason(exc)})
            continue
        if plugin and facts.coordinates and facts.coordinates != coordinates:
            correct = Gav(*facts.coordinates, entry.extension).maven_path
            report.misplaced.append({'path': entry.path, 'correct_path': correct})
            continue

        reason = plausible(facts.released, parse_time(entry.modified))
        if reason is None and facts.released is not None:
            created = modified = artifactory_time(facts.released)
        else:
            created, modified = entry.created, entry.modified
            report.dates_from_repository.append({'path': entry.path, 'reason': reason})
        index.rows.append(_row(key, entry.directory, entry.name, sha256=entry.sha256, sha1=sha1, size=entry.size, created=created, modified=modified))
        if plugin:
            index.plugins.add(coordinates)
        if facts.manifest is not None:
            index.manifests[entry.path] = facts.manifest
        if plugin and facts.description is not None:
            index.descriptions[coordinates] = facts.description

        real = poms.get(pom_path)
        if real is not None:
            try:
                verdict = _pom_verdict(backend, cache, real, coordinates)
                real_sha1 = _sha1(backend, cache, real) if verdict is None else ''
            except Exception as exc:  # noqa: BLE001 - an unreadable POM must not stop the listing: it is replaced and reported
                verdict = f'unreadable: {_reason(exc)}'
            if verdict is None:
                index.poms[pom_path] = None
                index.rows.append(_row(key, real.directory, real.name, sha256=real.sha256, sha1=real_sha1, size=real.size, created=created, modified=modified))
                continue
            report.replaced_poms.append({'path': pom_path, 'reason': verdict, 'url': PUBLIC_RELEASES_URL + pom_path})
        if facts.pom is not None and unusable_reason(facts.pom, coordinates) is None:
            index.poms[pom_path] = facts.pom
            directory, _, name = pom_path.rpartition('/')
            index.rows.append(_row(key, directory, name, sha256=hashlib.sha256(facts.pom).hexdigest(), sha1=hashlib.sha1(facts.pom, usedforsecurity=False).hexdigest(), size=len(facts.pom), created=created, modified=modified))
            report.derived_poms.append({'gav': ':'.join(coordinates), 'url': PUBLIC_RELEASES_URL + pom_path})
        else:
            report.needs_pom.append({'gav': ':'.join(coordinates), 'url': PUBLIC_RELEASES_URL + pom_path})

    for path, entry in sorted(poms.items()):  # POMs without an archive: parent POMs and the like
        if path in consumed:
            continue
        gav = to_gav(RepoFile(entry.directory, entry.name))
        if gav is None:
            continue
        try:
            verdict = _pom_verdict(backend, cache, entry, (gav.group_id, gav.artifact_id, gav.version))
            sha1 = _sha1(backend, cache, entry) if verdict is None else ''
        except Exception as exc:  # noqa: BLE001 - an unreadable POM must not stop the listing: it is left out and reported
            verdict, sha1 = f'unreadable: {_reason(exc)}', ''
        if verdict is None:
            index.poms[path] = None
            index.rows.append(_row(key, entry.directory, entry.name, sha256=entry.sha256, sha1=sha1, size=entry.size, created=entry.created, modified=entry.modified))
        else:
            report.replaced_poms.append({'path': path, 'reason': verdict, 'url': PUBLIC_RELEASES_URL + path})
    cache.prune()
    return index


def write_report(report: Report, path: Path) -> None:
    """Atomically, mode 0600: the paths and URLs in it are for operators, not the public."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.report-')
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(asdict(report), out, indent=2, sort_keys=True)
        out.write('\n')
    Path(tmp).replace(path)


def read_report(path: Path) -> dict | None:
    """Return the last report written, None when there is none or it is not a JSON object."""
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return None
    return value if isinstance(value, dict) else None
