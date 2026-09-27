"""uc doctor --download-poms, --download-dependencies and --download-war: stage what the report finds missing (docs/how-to/procure-missing-files.md)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, TextIO

from uc.repo_adapter.backends import Http

from . import ProcureError
from .dependencies import resolve_dependencies
from .plan import plan_cores, plan_poms
from .stager import Stager
from .staging import Staging
from .upstream import Upstream

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from uc.config import Settings
    from uc.doctor import DoctorReport, Inspection
    from uc.versions import VersionTool


def check_download_allowed(settings: Settings) -> None:
    """Refuse downloads in air-gapped mode unless PROCUREMENT_SOURCE_URL names a source the update center can reach."""
    if settings.mode == 'airgapped' and not settings.procurement_source_set:
        msg = 'downloads need repo.jenkins-ci.org, which air-gapped mode cannot reach: run them where the update center is online, or set PROCUREMENT_SOURCE_URL to a copy it can reach'
        raise ProcureError(msg)


def download(
    inspection: Inspection,
    settings: Settings,
    *,
    poms: bool,
    dependencies: bool,
    wars: Sequence[str],
    staging_dir: Path,
    versions: VersionTool,
    out: TextIO = sys.stdout,
    upstream: Upstream | None = None,
) -> int:
    """Stage what was asked for into staging_dir; return 0 when all of it is there, 1 otherwise."""
    check_download_allowed(settings)
    cores = plan_cores(wars, inspection.report)
    staging = Staging.open(staging_dir, settings.procurement_source_url)
    source = upstream or Upstream(settings.procurement_source_url, Http(settings.repo_ca_file), limit=settings.repo_max_archive_mb * 1024 * 1024)
    stager = Stager(staging, source, out)
    print(f'\nStaging into {staging_dir} from {settings.procurement_source_url}:', file=out)
    try:
        for wanted in plan_poms(inspection.report) if poms else []:
            stager.stage(wanted)
        for note in cores.notes:
            print(note, file=out)
        for wanted in cores.files:
            stager.stage(wanted)
        if dependencies:
            resolve_dependencies(inspection, stager, versions)
    finally:
        staging.save()
    _newer_cores(staging, inspection.report, versions, out)
    print(f'{stager.staged} staged, {stager.kept} kept, {stager.problems} problem(s); review {staging_dir}, then run uc doctor --push', file=out)
    return 1 if stager.problems else 0


def _newer_cores(staging: Staging, report: DoctorReport, versions: VersionTool, out: TextIO) -> None:
    """Note the staged plugins that require a newer Jenkins than the newest core in the repository."""
    if not report.cores:
        return
    newest = report.cores[-1]
    needing = [(path, staged.required_core) for path, staged in sorted(staging.files.items()) if staged.required_core]
    order = versions.compare_many([(core, newest) for _, core in needing])
    for (path, core), cmp in zip(needing, order, strict=True):
        if cmp > 0:
            print(f'note      {path}  (requires Jenkins {core}, newer than {newest}, the newest core in the repository)', file=out)
