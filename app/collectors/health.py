"""Parsers for Health Auto Export (Export Version 2) payloads.

Everything here is a pure function: dicts in, dicts out. No database, no
FastAPI, no network.
"""

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

SOURCE = "apple_health"
SLEEP_METRIC = "sleep_analysis"
HAE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S %z"

KJ_PER_KCAL = 4.184
KM_PER_MILE = 1.609344

# Sleep stages arrive in hours under these keys.
SLEEP_STAGE_KEYS = {
    "core_min": "core",
    "deep_min": "deep",
    "rem_min": "rem",
}


def parse_hae_datetime(value: str) -> datetime:
    """Parse '2024-02-05 23:00:00 -0800' into an aware datetime."""
    # The %z in HAE_DATETIME_FORMAT is what makes this aware; ruff cannot see it.
    return datetime.strptime(value.strip(), HAE_DATETIME_FORMAT)  # noqa: DTZ007


def optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_hae_datetime(str(value))
    except ValueError:
        logger.warning("unparseable timestamp %r", value)
        return None


def optional_date(value: Any) -> date | None:
    """Accept '2024-02-06' or a full HAE timestamp and keep the calendar day."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.warning("unparseable date %r", value)
        return None


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def hours_to_minutes(value: Any) -> float | None:
    hours = optional_float(value)
    return round(hours * 60, 2) if hours is not None else None


def quantity(field: Any) -> tuple[float | None, str]:
    """Unpack a {'qty': ..., 'units': ...} object that may be missing entirely."""
    if not isinstance(field, dict):
        return None, ""
    return optional_float(field.get("qty")), str(field.get("units") or "").strip()


# --- Sleep --------------------------------------------------------------


def parse_sleep(metric: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn one sleep_analysis metric into SleepSession rows."""
    rows: list[dict[str, Any]] = []

    for entry in metric.get("data") or []:
        night_of = optional_date(entry.get("date"))
        if night_of is None:
            logger.warning("sleep entry without a usable date, skipping")
            continue

        total = optional_float(entry.get("totalSleep"))
        asleep = total if total is not None else optional_float(entry.get("asleep"))
        if not asleep:
            logger.debug("no sleep recorded for %s, skipping", night_of)
            continue

        # start_at/end_at are NOT NULL, so fall back to the in-bed window.
        start_at = optional_datetime(entry.get("sleepStart")) or optional_datetime(
            entry.get("inBedStart")
        )
        end_at = optional_datetime(entry.get("sleepEnd")) or optional_datetime(
            entry.get("inBedEnd")
        )
        if start_at is None or end_at is None:
            logger.warning(
                "sleep entry for %s has no usable window, skipping", night_of
            )
            continue

        row = {
            "source": SOURCE,
            "external_id": f"sleep:{night_of.isoformat()}",
            "night_of": night_of,
            "start_at": start_at,
            "end_at": end_at,
            "asleep_min": round(asleep * 60, 2),
            "in_bed_min": hours_to_minutes(entry.get("inBed")),
            "awake_min": None,
            "raw": entry,
        }
        for column, key in SLEEP_STAGE_KEYS.items():
            row[column] = hours_to_minutes(entry.get(key))
        rows.append(row)

    return rows


# --- Workouts -----------------------------------------------------------


def active_kcal(workout: dict[str, Any]) -> float | None:
    qty, units = quantity(workout.get("activeEnergyBurned"))
    if qty is None:
        return None
    if units.lower() == "kj":
        return round(qty / KJ_PER_KCAL, 2)
    return round(qty, 2)


def distance_km(workout: dict[str, Any]) -> float | None:
    qty, units = quantity(workout.get("distance"))
    if qty is None:
        return None
    unit = units.lower()
    if unit == "mi":
        return round(qty * KM_PER_MILE, 4)
    if unit == "m":
        return round(qty / 1000, 4)
    if unit in {"km", ""}:
        return round(qty, 4)
    logger.warning("unknown distance unit %r, storing as-is", units)
    return round(qty, 4)


def avg_heart_rate(workout: dict[str, Any]) -> float | None:
    qty, _ = quantity(workout.get("avgHeartRate"))
    if qty is not None:
        return qty
    heart_rate = workout.get("heartRate")
    if isinstance(heart_rate, dict):
        qty, _ = quantity(heart_rate.get("avg"))
    return qty


def parse_workout(workout: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one workout object into a Workout row, or None if unusable."""
    start_at = optional_datetime(workout.get("start"))
    if start_at is None:
        logger.warning("workout %r has no start, skipping", workout.get("id"))
        return None

    # external_id is NOT NULL and drives the upsert, so synthesise one if needed.
    external_id = str(workout.get("id") or "").strip()
    if not external_id:
        external_id = f"workout:{start_at.isoformat()}"

    duration_seconds = optional_float(workout.get("duration"))

    return {
        "source": SOURCE,
        "external_id": external_id,
        "workout_type": str(workout.get("name") or "Unknown"),
        "start_at": start_at,
        "end_at": optional_datetime(workout.get("end")),
        "duration_min": (
            round(duration_seconds / 60, 2) if duration_seconds is not None else None
        ),
        "active_kcal": active_kcal(workout),
        "distance_km": distance_km(workout),
        "avg_hr": avg_heart_rate(workout),
        "raw": workout,
    }


# --- Payload ------------------------------------------------------------


def payload_summary(body: dict[str, Any]) -> tuple[list[str], int]:
    """What the phone actually sent: (metric names, workout object count).

    Compare against the parsed counts to tell "HealthKit returned nothing" apart
    from "we received data and dropped it".
    """
    data = body.get("data")
    if not isinstance(data, dict):
        return [], 0

    names = [
        str(metric.get("name"))
        for metric in data.get("metrics") or []
        if isinstance(metric, dict)
    ]
    return names, len(data.get("workouts") or [])


def parse_payload(
    body: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a Health Auto Export payload into (sleep rows, workout rows)."""
    data = body.get("data") or {}

    sleep_rows: list[dict[str, Any]] = []
    for metric in data.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        if metric.get("name") == SLEEP_METRIC:
            sleep_rows.extend(parse_sleep(metric))

    workout_rows: list[dict[str, Any]] = []
    for workout in data.get("workouts") or []:
        if not isinstance(workout, dict):
            continue
        row = parse_workout(workout)
        if row is not None:
            workout_rows.append(row)

    return sleep_rows, workout_rows
