from datetime import date
from zoneinfo import ZoneInfo

import pytest

from app.timerange import (
    DateRangeError,
    last_n_days,
    local_day_bounds,
    resolve_range,
)

CENTRAL = ZoneInfo("America/Chicago")


def test_local_day_starts_at_local_midnight_in_utc():
    start, end = local_day_bounds(date(2026, 9, 26), CENTRAL)

    # CDT is UTC-5, so the local day spans two UTC dates.
    assert start.isoformat() == "2026-09-26T05:00:00+00:00"
    assert end.isoformat() == "2026-09-27T05:00:00+00:00"


def test_winter_day_uses_standard_offset():
    start, _ = local_day_bounds(date(2026, 1, 15), CENTRAL)

    assert start.isoformat() == "2026-01-15T06:00:00+00:00"


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)],
)
def test_daylight_saving_days_are_not_24_hours(day, hours):
    start, end = local_day_bounds(day, CENTRAL)

    assert (end - start).total_seconds() / 3600 == hours


def test_defaults_to_thirty_days_ending_today():
    span = resolve_range(None, None, CENTRAL)

    assert span.days == 30
    assert span.end == last_n_days(1, CENTRAL).end


def test_single_day_range_is_one_day():
    span = resolve_range(date(2026, 5, 1), date(2026, 5, 1), CENTRAL)

    assert span.days == 1
    assert span.start_utc < span.end_utc


def test_rejects_backwards_range():
    with pytest.raises(DateRangeError, match="must not be after"):
        resolve_range(date(2026, 5, 2), date(2026, 5, 1), CENTRAL)


def test_rejects_range_wider_than_the_maximum():
    with pytest.raises(DateRangeError, match="exceeds"):
        resolve_range(date(2024, 1, 1), date(2026, 12, 31), CENTRAL)


def test_accepts_a_full_leap_year():
    span = resolve_range(date(2024, 1, 1), date(2024, 12, 31), CENTRAL)

    assert span.days == 366
