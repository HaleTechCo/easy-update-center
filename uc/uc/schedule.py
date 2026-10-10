"""The scheduler service: a first release straight away, then supercronic runs uc generate on GENERATE_SCHEDULE and a recorded uc doctor report on DOCTOR_SCHEDULE (docs/reference/cli.md#uc-schedule)."""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from . import publish
from .errors import UcError
from .generate import generate

if TYPE_CHECKING:
    from collections.abc import Callable

    from .config import Settings

CRONTAB = Path('/tmp/uc-crontab')  # noqa: S108 - the container's root is read-only; /tmp is its private tmpfs (compose.yaml)
CRON_FIELDS = 5


class ScheduleError(UcError):
    """A schedule is neither a 5-field cron expression nor an @macro."""


def job_line(schedule: str, setting: str, command: str) -> str:
    """Return the crontab line that runs command on schedule; setting names the schedule in the error."""
    fields = schedule.split()
    if not (len(fields) == CRON_FIELDS or (len(fields) == 1 and fields[0].startswith('@'))):
        msg = f'{setting} must be a 5-field cron expression or an @macro (got {schedule!r})'
        raise ScheduleError(msg)
    return f'{" ".join(fields)} {command}\n'


def crontab_text(settings: Settings) -> str:
    """Return the scheduler's crontab: uc generate, and a recorded uc doctor report when DOCTOR_SCHEDULE is set."""
    text = job_line(settings.generate_schedule, 'GENERATE_SCHEDULE', 'uc generate --wait')
    if settings.doctor_schedule:
        text += job_line(settings.doctor_schedule, 'DOCTOR_SCHEDULE', 'uc doctor --record')
    return text


def run_schedule(
    settings: Settings,
    *,
    generate_once: Callable[[], object] | None = None,
    exec_: Callable[[str, list[str], dict[str, str]], object] = os.execvpe,
    crontab: Path = CRONTAB,
) -> None:
    """Check the schedules, generate a first release if none is live, then replace this process with supercronic.

    A bad schedule raises ScheduleError before anything else; a failed first run is logged and scheduling goes ahead.
    With the default exec_ (os.execvpe) this never returns.
    """
    text = crontab_text(settings)
    if publish.live_release(settings.data_dir) is None:
        print('uc schedule: no release is live yet, generating now', flush=True)
        try:
            if generate_once is None:
                generate(settings)
            else:
                generate_once()
        except Exception:  # noqa: BLE001 - whatever fails, keep scheduling; status.json records the failure
            traceback.print_exc()
    crontab.write_text(text, encoding='utf-8')
    exec_('supercronic', ['supercronic', '-passthrough-logs', str(crontab)], dict(os.environ))
