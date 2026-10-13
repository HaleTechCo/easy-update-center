"""uc procure stage --dependencies: stage each missing required dependency.

At the highest minimum any plugin declares, and theirs in turn.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from uc.aql import Gav, RepoFile, to_gav
from uc.doctor import parse_dependencies, parse_manifest, required_jenkins_version
from uc.pom import dependency_group
from uc.repo_adapter.archive import (
    MANIFEST,
    SMALL_ENTRY_LIMIT,
    ArchiveError,
    open_archive,
    read_entry,
)

from . import ProcureError
from .stager import Wanted

if TYPE_CHECKING:
    from pathlib import Path

    from uc.doctor import Inspection
    from uc.versions import VersionTool

    from .stager import Stager

FALLBACK_GROUPS = (
    'org.jenkins-ci.plugins',
    'io.jenkins.plugins',
    'org.jenkins-ci.plugins.workflow',
    'org.jenkinsci.plugins',
    'io.jenkins.blueocean',
)
ARCHIVE_EXTENSIONS = ('hpi', 'jpi')
# untrusted manifest values: safe in a path and in VersionTool's line protocol
_COORDINATE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.+-]*$')
# untrusted POM values: dot-separated names, each a path segment
_GROUP = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9][A-Za-z0-9_-]*)*$')


@dataclass(frozen=True)
class Need:
    """A dependency to satisfy.

    Its artifactId, the minimum version asked for, who asked, and a group to try first.
    """

    artifact: str
    version: str
    required_by: str
    group: str | None


def archive_manifest(file: Path) -> dict[str, str]:
    """Return the main section of a staged archive's META-INF/MANIFEST.MF."""
    try:
        with file.open('rb') as stream, open_archive(stream) as archive:
            data = read_entry(archive, MANIFEST, SMALL_ENTRY_LIMIT)
    except (OSError, ArchiveError) as exc:
        msg = f'{file.name}: {exc}'
        raise ProcureError(msg) from exc
    if data is None:
        msg = f'{file.name} has no {MANIFEST}'
        raise ProcureError(msg)
    return parse_manifest(data.decode('utf-8', 'replace'))


def resolve_dependencies(
    inspection: Inspection, stager: Stager, versions: VersionTool
) -> None:
    """Stage every missing required dependency in the report, and those they bring.

    Until nothing new is needed.
    """
    repository = versions.max_by_key(
        (g.artifact_id, g.version) for g in inspection.listing.plugins
    )
    needs: list[Need] = []
    for detail in inspection.report.missing_dependency_details:
        requirer = Gav(
            detail['plugin_group'], detail['plugin'], detail['plugin_version'], 'hpi'
        )
        group = dependency_group(inspection.source.pom(requirer), detail['dependency'])
        needs.append(
            Need(
                detail['dependency'],
                detail['required'],
                f'{detail["plugin"]} {detail["plugin_version"]}',
                group,
            )
        )
    # (artifactId, minimum) that could not be staged this run
    failed: set[tuple[str, str]] = set()
    # (artifactId, version) staged or checked this run: each once, so cycles end
    visited: set[tuple[str, str]] = set()
    while needs:
        needs = _round(
            _valid(needs, stager),
            repository,
            stager,
            versions,
            failed=failed,
            visited=visited,
        )


def _valid(needs: list[Need], stager: Stager) -> list[Need]:
    kept = []
    for need in needs:
        if _COORDINATE.match(need.artifact) and _COORDINATE.match(need.version):
            # a group that is not one never reaches a URL:
            # the known groups are tried instead
            kept.append(
                need
                if need.group is None or _GROUP.match(need.group)
                else dataclasses.replace(need, group=None)
            )
        else:
            stager.problem(
                f'{need.artifact} {need.version}',
                f'not a plugin name and version (required by {need.required_by})',
            )
    return kept


def _staged_plugins(stager: Stager) -> dict[str, Gav]:
    result: dict[str, Gav] = {}
    for path, staged in stager.staging.files.items():
        directory, _, name = path.rpartition('/')
        gav = to_gav(RepoFile(directory, name))
        if (
            staged.kind == 'dependency'
            and gav is not None
            and gav.extension in ARCHIVE_EXTENSIONS
        ):
            result[gav.artifact_id] = gav
    return result


def _highest(needs: list[Need], versions: VersionTool) -> list[Need]:
    top = versions.max_by_key((need.artifact, need.version) for need in needs)
    chosen: dict[str, Need] = {}
    for need in needs:
        if need.version == top[need.artifact]:
            chosen.setdefault(need.artifact, need)
    return [chosen[artifact] for artifact in sorted(chosen)]


def _met(
    needs: list[Need],
    repository: dict[str, str],
    staged: dict[str, Gav],
    versions: VersionTool,
) -> dict[str, str]:
    """Map each need's artifactId to what already satisfies it.

    'repository' before 'staged'; needs nothing satisfies are left out.
    """
    candidates = [
        (need, where, have)
        for need in needs
        for where, have in (
            ('repository', repository.get(need.artifact)),
            (
                'staged',
                staged[need.artifact].version if need.artifact in staged else None,
            ),
        )
        if have is not None
    ]
    order = versions.compare_many(
        [(have, need.version) for need, _, have in candidates]
    )
    met: dict[str, str] = {}
    for (need, where, _), cmp in zip(candidates, order, strict=True):
        if cmp >= 0:
            met.setdefault(need.artifact, where)
    return met


def _round(
    needs: list[Need],
    repository: dict[str, str],
    stager: Stager,
    versions: VersionTool,
    *,
    failed: set[tuple[str, str]],
    visited: set[tuple[str, str]],
) -> list[Need]:
    if not needs:
        return []
    staged = _staged_plugins(stager)
    chosen = _highest(needs, versions)
    met = _met(chosen, repository, staged, versions)
    following: list[Need] = []
    for need in chosen:
        where = met.get(need.artifact)
        if where == 'repository':
            continue
        # staged earlier: stage() keeps it when intact and downloads it again when not,
        # and its own needs are followed again
        if where == 'staged':
            gav = staged[need.artifact]
            if (gav.artifact_id, gav.version) in visited:
                continue
        else:
            if (need.artifact, need.version) in failed:
                continue
            gav = _locate(need, stager)
            if gav is None:
                failed.add((need.artifact, need.version))
                continue
            previous = staged.get(need.artifact)
            # a higher minimum supersedes what an earlier run staged
            if previous is not None:
                for old in (
                    previous.maven_path,
                    previous.with_extension('pom').maven_path,
                ):
                    if (
                        old in stager.staging.files
                        and stager.staging.files[old].kind == 'dependency'
                    ):
                        stager.staging.remove(old)
        visited.add((gav.artifact_id, gav.version))
        reason = f'{need.required_by} requires {need.artifact} {need.version}'
        if not (
            stager.stage(Wanted(gav.maven_path, 'dependency', reason))
            and stager.stage(
                Wanted(gav.with_extension('pom').maven_path, 'dependency', reason)
            )
        ):
            failed.add((need.artifact, need.version))
            continue
        following += _needs_of(gav, stager)
    return following


def _locate(need: Need, stager: Stager) -> Gav | None:
    groups = [need.group] if need.group else []
    groups += [group for group in FALLBACK_GROUPS if group != need.group]
    label = f'{need.artifact} {need.version}'
    for group in groups:
        for extension in ARCHIVE_EXTENSIONS:
            gav = Gav(group, need.artifact, need.version, extension)
            try:
                found = stager.upstream.sha1(gav.maven_path) is not None
            except ProcureError as exc:
                stager.problem(label, str(exc))
                return None
            if found:
                return gav
    stager.problem(
        label,
        (
            f'not found at the source under {", ".join(groups)}; '
            f'required by {need.required_by}'
        ),
    )
    return None


def _needs_of(gav: Gav, stager: Stager) -> list[Need]:
    try:
        manifest = archive_manifest(stager.staging.root / gav.maven_path)
    except ProcureError as exc:
        stager.problem(gav.maven_path, str(exc))
        return []
    staged = stager.staging.files[gav.maven_path]
    stager.staging.files[gav.maven_path] = dataclasses.replace(
        staged, required_core=required_jenkins_version(manifest)
    )
    pom_file = stager.staging.root / gav.with_extension('pom').maven_path
    pom = pom_file.read_bytes() if pom_file.is_file() else None
    requirer = f'{gav.artifact_id} {gav.version}'
    return [
        Need(name, version, requirer, dependency_group(pom, name))
        for name, version, optional in parse_dependencies(
            manifest.get('Plugin-Dependencies', '')
        )
        if not optional
    ]
