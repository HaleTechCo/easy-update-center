"""uc procure stage --poms, --dependencies and --war.

Stage what the report finds missing (docs/how-to/procure-missing-files.md).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, TextIO

from uc.http import Http

from . import ProcureError
from .dependencies import resolve_dependencies
from .plan import plan_cores, plan_poms
from .stager import Stager
from .staging import Staging, review_line
from .upstream import Upstream

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from uc.config import Settings
    from uc.doctor import DoctorReport, Inspection
    from uc.versions import VersionTool


def downloads_allowed(settings: Settings) -> bool:
    """Return whether uc procure stage may download.

    Online, or air-gapped with PROCUREMENT_SOURCE_URL set.
    """
    return settings.mode != 'airgapped' or settings.procurement_source_set


def check_download_allowed(settings: Settings) -> None:
    """Refuse downloads in air-gapped mode.

    Unless PROCUREMENT_SOURCE_URL names a source the update center can reach.
    """
    if not downloads_allowed(settings):
        msg = (
            'downloads need repo.jenkins-ci.org, which air-gapped mode cannot reach: '
            'run them where the update center is online, or set PROCUREMENT_SOURCE_URL '
            'to a copy it can reach'
        )
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
    """Stage what was asked for into staging_dir.

    Return 0 when all of it is there, 1 otherwise.
    """
    check_download_allowed(settings)
    cores = plan_cores(wars, inspection.report)
    staging = Staging.open(staging_dir, settings.procurement_source_url)
    source = upstream or Upstream(
        settings.procurement_source_url,
        Http(settings.repo_ca_file),
        limit=settings.repo_max_archive_mb * 1024 * 1024,
    )
    stager = Stager(staging, source, out)
    print(
        f'\nStaging into {staging_dir} from {settings.procurement_source_url}:',
        file=out,
    )
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
    report = inspection.report
    _newer_cores(staging, report, versions, out)
    # manifests missing from the cache hide dependencies and required cores
    if report.unchecked or report.cores_unchecked:
        print(
            (
                f'warning   {report.unchecked} plugin release(s) not checked for '
                f'dependencies and {report.cores_unchecked} for required cores (POM or '
                'manifest unavailable); run with --fetch so dependencies and required '
                'cores are complete'
            ),
            file=out,
        )
    print(
        f'{stager.staged} staged, {stager.kept} kept, {stager.problems} problem(s)',
        file=out,
    )
    if staging.files:
        print(review_line(staging_dir, len(staging.files)), file=out)
    return 1 if stager.problems else 0


def _newer_cores(
    staging: Staging, report: DoctorReport, versions: VersionTool, out: TextIO
) -> None:
    """Note the staged plugins that require a newer Jenkins.

    Newer than the newest core in the repository.
    """
    if not report.cores:
        return
    newest = report.cores[-1]
    needing = [
        (path, staged.required_core)
        for path, staged in sorted(staging.files.items())
        if staged.required_core
    ]
    order = versions.compare_many([(core, newest) for _, core in needing])
    for (path, core), cmp in zip(needing, order, strict=True):
        if cmp > 0:
            print(
                (
                    f'note      {path}  (requires Jenkins {core}, newer than {newest}, '
                    'the newest core in the repository)'
                ),
                file=out,
            )
