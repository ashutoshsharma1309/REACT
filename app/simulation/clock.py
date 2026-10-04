"""Plant time helpers shared by the generator, scenarios and detection engines.

All timestamps are naive UTC. Astra's Bangalore plant runs two shifts, 06:30-22:30 IST,
which is 01:00-17:00 UTC. The plant is closed on Sundays.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

SHIFT_START_HOUR_UTC = 1
SHIFT_END_HOUR_UTC = 17
OPERATING_HOURS_PER_DAY = SHIFT_END_HOUR_UTC - SHIFT_START_HOUR_UTC  # 16h


def is_working_day(d: date) -> bool:
    return d.weekday() != 6  # Sunday off


def is_operating(ts: datetime) -> bool:
    return is_working_day(ts.date()) and SHIFT_START_HOUR_UTC <= ts.hour < SHIFT_END_HOUR_UTC


def shift_progress(ts: datetime) -> float:
    """0.0 at shift start .. 1.0 at shift end (used for warm-up curves)."""
    if not is_operating(ts):
        return 0.0
    return (ts.hour + ts.minute / 60 - SHIFT_START_HOUR_UTC) / OPERATING_HOURS_PER_DAY


def truncate_to_hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)
