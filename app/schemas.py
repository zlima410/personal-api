"""Response models.

None of these expose the `raw` payload column or database ids: those are
storage details, and `raw` can contain far more than the API should hand out.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models import BookStatus, SyncStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SleepSessionOut(ORMModel):
    source: str
    night_of: date
    start_at: datetime
    end_at: datetime
    asleep_min: float | None = None
    in_bed_min: float | None = None
    core_min: float | None = None
    deep_min: float | None = None
    rem_min: float | None = None
    awake_min: float | None = None


class WorkoutOut(ORMModel):
    source: str
    workout_type: str
    start_at: datetime
    end_at: datetime | None = None
    duration_min: float | None = None
    active_kcal: float | None = None
    distance_km: float | None = None
    avg_hr: float | None = None


class CommitOut(ORMModel):
    source: str
    repo: str
    sha: str
    message: str
    committed_at: datetime
    additions: int | None = None
    deletions: int | None = None
    url: str


class BookOut(ORMModel):
    source: str
    title: str
    authors: list[str]
    status: BookStatus
    started_on: date | None = None
    finished_on: date | None = None
    rating: float | None = None
    cover_url: str | None = None


class SyncRunOut(ORMModel):
    source: str
    status: SyncStatus
    started_at: datetime
    finished_at: datetime | None = None
    records_upserted: int
    error: str | None = None
    cursor: str | None = None


class DayRollup(BaseModel):
    """Everything that happened on one local calendar day."""

    date: date
    sleep: SleepSessionOut | None = None
    workouts: list[WorkoutOut] = []
    commits: list[CommitOut] = []
    finished_books: list[BookOut] = []
    currently_reading: list[BookOut] = []


class PublicSummary(BaseModel):
    """Coarse, non-identifying totals safe to serve without a key."""

    start: date
    end: date
    days: int
    avg_sleep_hours: float | None = None
    workout_count: int
    commit_count: int
    currently_reading: list[str] = []


class SyncTriggered(BaseModel):
    source: str
    upserted: int
