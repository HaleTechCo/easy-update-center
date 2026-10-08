"""What uc procure stage --poms and --war stage, worked out from the doctor report."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from uc.aql import WAR_ARTIFACT, WAR_GROUP, Gav
from uc.doctor import CORE_VERSION

from . import ProcureError
from .stager import Wanted
from .staging import DEFAULT_STAGING, shown_staging

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from uc.doctor import DoctorReport

REQUIRED = 'required'


def pom_path(gav: str) -> str:
    """Return the Maven path of the .pom for 'group:artifact:version'."""
    group, artifact, version = gav.split(':')
    return Gav(group, artifact, version, 'pom').maven_path


def plan_poms(report: DoctorReport) -> list[Wanted]:
    """Return every POM the report names (missing, derived from an archive, or unusable) once, in path order."""
    wanted: dict[str, Wanted] = {}
    adapter = report.repo_adapter
    if adapter is None:
        for archive in report.hpi_without_pom:
            path = archive.rsplit('.', 1)[0] + '.pom'
            wanted[path] = Wanted(path, 'pom', f'no .pom beside {archive}: the generator drops it without one')
        return [wanted[path] for path in sorted(wanted)]
    for item in adapter.get('derived_poms') or []:
        path = pom_path(item['gav'])
        wanted[path] = Wanted(path, 'pom', f'real POM for {item["gav"]}; the one derived from its archive works meanwhile')
    for item in adapter.get('needs_pom') or []:
        path = pom_path(item['gav'])
        wanted[path] = Wanted(path, 'pom', f'{item["gav"]} has no POM in the repository or its archive: the generator drops it without one')
    for item in adapter.get('replaced_poms') or []:
        wanted[item['path']] = Wanted(item['path'], 'pom', f'replaces an unusable .pom ({item["reason"]}): delete the one in the repository before pushing', replaces_unusable=True)
    return [wanted[path] for path in sorted(wanted)]


def suggest_stage(report: DoctorReport, *, downloads: bool = True, since: str | None = None, staging: Path = DEFAULT_STAGING) -> str | None:
    """Return uc doctor's next: line when the report finds something uc procure stage can fetch, else None.

    downloads is False where uc procure stage would refuse to download (check_download_allowed): the line then says to
    place the files in the staging folder and record them, or to set PROCUREMENT_SOURCE_URL. since is uc doctor's
    --since, carried over with the required cores, which need it when the repository holds no core.
    """
    found = [
        ('--poms', 'PROCUREMENT_POMS=true', bool(plan_poms(report))),
        ('--dependencies', 'PROCUREMENT_DEPENDENCIES=true', bool(report.missing_dependencies)),
        ('--war required', 'PROCUREMENT_WARS=required', bool(report.missing_cores)),
        (f'--since {since}', f'DOCTOR_SINCE={since}', since is not None and bool(report.missing_cores)),
    ]
    flags = [flag for flag, _, present in found if present]
    if not flags:
        return None
    if not downloads:
        return f'next: place the files the report lists in {shown_staging(staging)} and run uc procure record, or set PROCUREMENT_SOURCE_URL to a copy of repo.jenkins-ci.org the update center can reach, then uc procure stage {" ".join(flags)}'
    settings = [setting for _, setting, present in found if present]
    return f'next: uc procure stage {" ".join(flags)} (or set {", ".join(settings)})'


def check_war_args(values: Sequence[str]) -> None:
    """Refuse --war values that are neither a Jenkins version nor 'required', before anything runs."""
    for value in values:
        if value != REQUIRED and not CORE_VERSION.match(value):
            msg = f'--war takes a Jenkins version such as 2.479.1, or required (got {value!r})'
            raise ProcureError(msg)


@dataclass(frozen=True)
class CorePlan:
    """The core files to stage, and notes on versions left out."""

    files: list[Wanted] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def plan_cores(values: Sequence[str], report: DoctorReport) -> CorePlan:
    """Resolve --war values (versions, or 'required') into the .war and .pom of each core to stage."""
    check_war_args(values)
    stocked = set(report.cores)
    reasons: dict[str, str] = {}
    notes: list[str] = []
    for value in values:
        if value != REQUIRED:
            if value in stocked:
                notes.append(f'skipped   jenkins-war {value}  (already in the repository)')
            else:
                reasons[value] = 'requested'
            continue
        if report.required_cores is None:
            msg = '--war required is not available when the source is the public repo.jenkins-ci.org; name the versions instead'
            raise ProcureError(msg)
        if report.core_floor is None:
            msg = 'the repository holds no Jenkins core, so "required" has no floor: add --since <oldest version your controllers run>'
            raise ProcureError(msg)
        if not report.missing_cores:
            notes.append(f'no required core at or above {report.core_floor} is missing')
        for core in report.missing_cores:
            count = core['required_by']
            reasons.setdefault(core['version'], f'required by {count} plugin release{"" if count == 1 else "s"}, e.g. {core["example"]}')
    files: list[Wanted] = []
    for version, reason in reasons.items():
        war = Gav(WAR_GROUP, WAR_ARTIFACT, version, 'war')
        files += [Wanted(war.maven_path, 'core', reason), Wanted(war.with_extension('pom').maven_path, 'core', reason)]
    return CorePlan(files, notes)
