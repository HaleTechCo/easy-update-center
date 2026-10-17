"""The gap-filled listing the adapter serves, and its report.

See docs/architecture/repo-adapter.md#filling-gaps.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from uc.aql import Gav, RepoFile, to_gav
from uc.config import PUBLIC_RELEASES_URL, UnsafeFileError, read_untrusted
from uc.http import HttpError
from uc.pom import Coordinates, unusable_reason
from uc.timeutil import iso

from .archive import SMALL_ENTRY_LIMIT, ArchiveError, ArchiveFacts, plausible
from .backends import Backend, RepoEntry, artifactory_time, parse_time

if TYPE_CHECKING:
    import datetime as dt

    from .cache import Cache

ARCHIVE_EXTENSIONS = ('hpi', 'jpi', 'war')
REPORT_LISTS = (
    'derived_poms',
    'replaced_poms',
    'needs_pom',
    'misplaced',
    'skipped',
    'dates_from_repository',
)


@dataclass
class Report:
    """What one listing filled in, replaced or left out, for operators and uc doctor.

    See docs/architecture/repo-adapter.md#report.
    """

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
    """One listing.

    The AQL rows, and what GETs resolve against until the next listing.
    """

    rows: list[dict] = field(default_factory=list)
    # path -> the bytes GETs serve: derived from the archive, or the repository's
    # as checked
    poms: dict[str, bytes] = field(default_factory=dict)
    # archive path -> MANIFEST.MF
    manifests: dict[str, bytes] = field(default_factory=dict)
    # plugin -> index.jelly from its .hpi
    descriptions: dict[Coordinates, bytes] = field(default_factory=dict)
    # the repository's .jar files (Artifact Keeper)
    jars: dict[str, RepoEntry] = field(default_factory=dict)
    # every plugin archive in the rows
    plugins: set[Coordinates] = field(default_factory=set)
    report: Report | None = None


def _reason(exc: Exception) -> str:
    """Our errors carry their own message; anything else also names its type."""
    return (
        str(exc)
        if isinstance(exc, (HttpError, ArchiveError))
        else f'{type(exc).__name__}: {exc}'
    )


def _row(
    key: str,
    directory: str,
    name: str,
    *,
    sha256: str,
    sha1: str,
    size: int,
    created: str,
    modified: str,
) -> dict:
    return {
        'repo': key,
        'path': directory,
        'name': name,
        'created': created,
        'modified': modified,
        'sha256': sha256,
        'actual_sha1': sha1,
        'size': size,
    }


def _facts(
    backend: Backend,
    cache: Cache,
    entry: RepoEntry,
    coordinates: Coordinates,
    *,
    plugin: bool,
) -> ArchiveFacts:
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


def _repository_pom(
    backend: Backend, cache: Cache, entry: RepoEntry, coordinates: Coordinates
) -> tuple[bytes | None, str | None]:
    """Return a repository POM's checked bytes and None, or None and why it is unusable.

    Rule 3. The bytes returned match the listing's sha256, are at most SMALL_ENTRY_LIMIT
    and pass unusable_reason; the adapter serves exactly them.
    """
    cached = cache.pom_verdict(entry.sha256)
    if cached:
        return None, cached
    if cached == '':
        data = cache.repository_pom(entry.sha256)
        if data is not None:
            return data, None
    data = backend.get(entry.path, SMALL_ENTRY_LIMIT)
    if data is None:
        reason = 'missing from the repository'
    elif hashlib.sha256(data).hexdigest() != entry.sha256:
        reason = "its content does not match the listing's sha256"
    else:
        reason = unusable_reason(data, coordinates)
    if reason is None and data is not None:
        cache.store_repository_pom(entry.sha256, data)
        cache.store_pom_verdict(entry.sha256, None)
        return data, None
    cache.store_pom_verdict(entry.sha256, reason)
    return None, reason


def build_index(backend: Backend, cache: Cache, *, key: str, now: dt.datetime) -> Index:
    """List the repository and fill its gaps.

    Archive dates, POMs derived from the archives, and the report.
    """
    entries = backend.list()
    report = Report(generated_at=iso(now), repo={'type': backend.type, 'key': key})
    index = Index(report=report)
    poms = {e.path: e for e in entries if e.extension == 'pom'}
    index.jars = {e.path: e for e in entries if e.extension == 'jar'}
    consumed: set[str] = set()

    for entry in sorted(
        (e for e in entries if e.extension in ARCHIVE_EXTENSIONS), key=lambda e: e.path
    ):
        report.archives += 1
        gav = to_gav(RepoFile(entry.directory, entry.name))
        # not in Maven layout: upstream rejects it, and uc doctor reports it
        if gav is None:
            index.rows.append(
                _row(
                    key,
                    entry.directory,
                    entry.name,
                    sha256=entry.sha256,
                    sha1=entry.sha1 or '',
                    size=entry.size,
                    created=entry.created,
                    modified=entry.modified,
                )
            )
            continue
        pom_path = gav.with_extension('pom').maven_path
        consumed.add(pom_path)
        coordinates = (gav.group_id, gav.artifact_id, gav.version)
        plugin = entry.extension != 'war'
        try:
            facts = _facts(backend, cache, entry, coordinates, plugin=plugin)
            sha1 = _sha1(backend, cache, entry)
        # one bad archive must not stop the listing: it is dropped and reported
        except Exception as exc:  # noqa: BLE001
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
        index.rows.append(
            _row(
                key,
                entry.directory,
                entry.name,
                sha256=entry.sha256,
                sha1=sha1,
                size=entry.size,
                created=created,
                modified=modified,
            )
        )
        if plugin:
            index.plugins.add(coordinates)
        if facts.manifest is not None:
            index.manifests[entry.path] = facts.manifest
        if plugin and facts.description is not None:
            index.descriptions[coordinates] = facts.description

        real = poms.get(pom_path)
        if real is not None:
            try:
                data, verdict = _repository_pom(backend, cache, real, coordinates)
                real_sha1 = _sha1(backend, cache, real) if verdict is None else ''
            # an unreadable POM must not stop the listing: it is replaced and reported
            except Exception as exc:  # noqa: BLE001
                data, verdict = None, f'unreadable: {_reason(exc)}'
            if verdict is None and data is not None:
                index.poms[pom_path] = data
                index.rows.append(
                    _row(
                        key,
                        real.directory,
                        real.name,
                        sha256=real.sha256,
                        sha1=real_sha1,
                        size=real.size,
                        created=created,
                        modified=modified,
                    )
                )
                continue
            report.replaced_poms.append(
                {
                    'path': pom_path,
                    'reason': verdict,
                    'url': PUBLIC_RELEASES_URL + pom_path,
                }
            )
        if facts.pom is not None and unusable_reason(facts.pom, coordinates) is None:
            index.poms[pom_path] = facts.pom
            directory, _, name = pom_path.rpartition('/')
            index.rows.append(
                _row(
                    key,
                    directory,
                    name,
                    sha256=hashlib.sha256(facts.pom).hexdigest(),
                    sha1=hashlib.sha1(facts.pom, usedforsecurity=False).hexdigest(),
                    size=len(facts.pom),
                    created=created,
                    modified=modified,
                )
            )
            report.derived_poms.append(
                {'gav': ':'.join(coordinates), 'url': PUBLIC_RELEASES_URL + pom_path}
            )
        else:
            report.needs_pom.append(
                {'gav': ':'.join(coordinates), 'url': PUBLIC_RELEASES_URL + pom_path}
            )

    # POMs without an archive: parent POMs and the like
    for path, entry in sorted(poms.items()):
        if path in consumed:
            continue
        gav = to_gav(RepoFile(entry.directory, entry.name))
        if gav is None:
            # upstream would reject it: it is left out, and reported
            report.skipped.append(
                {'path': path, 'reason': 'not in Maven layout; left out of the listing'}
            )
            continue
        try:
            data, verdict = _repository_pom(
                backend, cache, entry, (gav.group_id, gav.artifact_id, gav.version)
            )
            sha1 = _sha1(backend, cache, entry) if verdict is None else ''
        # an unreadable POM must not stop the listing: it is left out and reported
        except Exception as exc:  # noqa: BLE001
            data, verdict, sha1 = None, f'unreadable: {_reason(exc)}', ''
        if verdict is None and data is not None:
            index.poms[path] = data
            index.rows.append(
                _row(
                    key,
                    entry.directory,
                    entry.name,
                    sha256=entry.sha256,
                    sha1=sha1,
                    size=entry.size,
                    created=entry.created,
                    modified=entry.modified,
                )
            )
        else:
            report.replaced_poms.append(
                {'path': path, 'reason': verdict, 'url': PUBLIC_RELEASES_URL + path}
            )
    cache.prune()
    return index


def write_report(report: Report, path: Path) -> None:
    """Atomically, mode 0600.

    The paths and URLs in it are for operators, not the public.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.report-')
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(asdict(report), out, indent=2, sort_keys=True)
        out.write('\n')
    Path(tmp).replace(path)


REPORT_LIMIT = 16 * 1024 * 1024
"""The largest report.json uc reads."""


def read_report(path: Path) -> dict | None:
    """Return the last report written.

    None when there is none, or it is not a JSON object, or it is a link, not a
    regular file or larger than REPORT_LIMIT: uc reads it from the adapter's volume,
    which the adapter can write.
    """
    try:
        data = read_untrusted(path, REPORT_LIMIT)
    except UnsafeFileError:
        return None
    if data is None:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
