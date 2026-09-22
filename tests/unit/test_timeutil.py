import datetime as dt

from uc.timeutil import iso, parse_iso, utcnow


def test_iso_roundtrip():
    moment = dt.datetime(2026, 9, 22, 3, 4, 5, tzinfo=dt.timezone.utc)
    assert iso(moment) == "2026-09-22T03:04:05Z"
    assert parse_iso("2026-09-22T03:04:05Z") == moment
    assert parse_iso("2026-09-22T05:04:05+02:00") == moment


def test_utcnow_is_aware_and_whole_seconds():
    now = utcnow()
    assert now.tzinfo is not None and now.microsecond == 0
