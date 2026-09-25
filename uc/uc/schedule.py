"""The scheduler service: a first release straight away, then supercronic on GENERATE_SCHEDULE (docs/reference/cli.md#uc-schedule)."""

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
    """GENERATE_SCHEDULE is neither a 5-field cron expression nor an @macro."""


def crontab_line(schedule: str) -> str:
    """Return the crontab line that runs `uc generate` on schedule."""
    fields = schedule.split()
    if not (len(fields) == CRON_FIELDS or (len(fields) == 1 and fields[0].startswith('@'))):
        msg = f'GENERATE_SCHEDULE must be a 5-field cron expression or an @macro (got {schedule!r})'
        raise ScheduleError(msg)
    return f'{" ".join(fields)} uc generate\n'


def run_schedule(
    settings: Settings,
    *,
    generate_once: Callable[[], object] | None = None,
    exec_: Callable[[str, list[str]], object] = os.execvp,
    crontab: Path = CRONTAB,
) -> None:
    """Generate a first release if none is live, then replace this process with supercronic.

    A failed first run is logged and scheduling goes ahead; with the default exec_ (os.execvp) this never returns.
    """
    line = crontab_line(settings.generate_schedule)
    if publish.live_release(settings.data_dir) is None:
        print('uc schedule: no release is live yet, generating now', flush=True)
        try:
            if generate_once is None:
                generate(settings)
            else:
                generate_once()
        except Exception:  # noqa: BLE001 - whatever fails, keep scheduling; status.json records the failure
            traceback.print_exc()
    crontab.write_text(line, encoding='utf-8')
    exec_('supercronic', ['supercronic', '-passthrough-logs', str(crontab)])
