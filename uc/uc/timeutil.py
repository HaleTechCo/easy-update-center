"""UTC timestamps in the one format used everywhere: YYYY-MM-DDTHH:MM:SSZ."""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value).astimezone(dt.UTC)
