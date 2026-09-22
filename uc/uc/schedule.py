"""The scheduler service: a first release straight away, then supercronic on GENERATE_SCHEDULE (spec §5)."""
from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Callable

from . import publish
from .config import Settings
from .errors import UcError

CRONTAB = Path("/tmp/uc-crontab")


class ScheduleError(UcError):
    pass


def crontab_line(schedule: str) -> str:
    fields = schedule.split()
    if not (len(fields) == 5 or (len(fields) == 1 and fields[0].startswith("@"))):
        raise ScheduleError(f"GENERATE_SCHEDULE must be a 5-field cron expression or an @macro (got {schedule!r})")
    return f"{' '.join(fields)} uc generate\n"


def run_schedule(
    settings: Settings,
    *,
    generate_once: Callable[[], object] | None = None,
    exec_: Callable[[str, list[str]], object] = os.execvp,
    crontab: Path = CRONTAB,
) -> None:
    line = crontab_line(settings.generate_schedule)
    if publish.live_release(settings.data_dir) is None:
        print("uc schedule: no release is live yet, generating now", flush=True)
        try:
            if generate_once is None:
                from .generate import generate

                generate(settings)
            else:
                generate_once()
        except Exception:  # keep scheduling; status.json records the failure
            traceback.print_exc()
    crontab.write_text(line, encoding="utf-8")
    exec_("supercronic", ["supercronic", "-passthrough-logs", str(crontab)])
