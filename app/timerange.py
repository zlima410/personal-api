"""Local-date to UTC-range helpers.

Timestamps are stored in UTC, but every question worth asking ("what did I do
on Tuesday?") is about a local calendar day. These pure functions translate
between the two so no router has to do it by hand.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings

DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 366
SUMMARY_DAYS = 7


class DateRangeError(ValueError):
    """The requested range is empty, backwards, or too wide."""


def local_timezone() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def today_local(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def start_of_local_day(day: date, tz: ZoneInfo) -> datetime:
    """The UTC instant at which this local calendar day begins."""
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC half-open interval [start, end) covering one local day."""
    return start_of_local_day(day, tz), start_of_local_day(day + timedelta(days=1), tz)


@dataclass(frozen=True)
class DateRange:
    """An inclusive span of local dates and the UTC window it maps onto."""

    start: date
    end: date
    start_utc: datetime
    end_utc: datetime

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def make_range(start: date, end: date, tz: ZoneInfo) -> DateRange:
    start_utc, _ = local_day_bounds(start, tz)
    _, end_utc = local_day_bounds(end, tz)
    return DateRange(start=start, end=end, start_utc=start_utc, end_utc=end_utc)


def resolve_range(
    start: date | None,
    end: date | None,
    tz: ZoneInfo,
    *,
    default_days: int = DEFAULT_RANGE_DAYS,
    max_days: int = MAX_RANGE_DAYS,
) -> DateRange:
    """Fill in defaults and validate. Raises DateRangeError on bad input."""
    resolved_end = end or today_local(tz)
    resolved_start = start or resolved_end - timedelta(days=default_days - 1)

    if resolved_start > resolved_end:
        raise DateRangeError(
            f"'from' ({resolved_start}) must not be after 'to' ({resolved_end})"
        )

    span = (resolved_end - resolved_start).days + 1
    if span > max_days:
        raise DateRangeError(f"range of {span} days exceeds the {max_days} day maximum")

    return make_range(resolved_start, resolved_end, tz)


def last_n_days(days: int, tz: ZoneInfo) -> DateRange:
    """The inclusive range ending today, local time."""
    end = today_local(tz)
    return make_range(end - timedelta(days=days - 1), end, tz)
