import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_read_key
from app.db import get_db
from app.models import Book, BookStatus, Commit, SleepSession, Workout
from app.schemas import (
    BookOut,
    CommitOut,
    DayRollup,
    PublicSummary,
    SleepSessionOut,
    WorkoutOut,
)
from app.timerange import (
    SUMMARY_DAYS,
    DateRange,
    DateRangeError,
    last_n_days,
    local_day_bounds,
    local_timezone,
    resolve_range,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["v1"],
    dependencies=[Depends(require_read_key)],
)
public_router = APIRouter(prefix="/v1/public", tags=["public"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

SessionDep = Annotated[Session, Depends(get_db)]


def date_range(
    start: Annotated[
        date | None,
        Query(alias="from", description="First local day, inclusive."),
    ] = None,
    end: Annotated[
        date | None,
        Query(alias="to", description="Last local day, inclusive."),
    ] = None,
) -> DateRange:
    """Resolve ?from=&to= into a UTC window, or fail with 422."""
    try:
        return resolve_range(start, end, local_timezone())
    except DateRangeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


RangeDep = Annotated[DateRange, Depends(date_range)]


@router.get("/sleep", response_model=list[SleepSessionOut])
def list_sleep(session: SessionDep, span: RangeDep) -> list[SleepSession]:
    # night_of is already the local night, so it is compared as a plain date.
    stmt = (
        select(SleepSession)
        .where(SleepSession.night_of >= span.start, SleepSession.night_of <= span.end)
        .order_by(SleepSession.night_of.desc())
    )
    return list(session.scalars(stmt))


@router.get("/workouts", response_model=list[WorkoutOut])
def list_workouts(
    session: SessionDep,
    span: RangeDep,
    workout_type: Annotated[
        str | None, Query(alias="type", description="Exact type, case-insensitive.")
    ] = None,
) -> list[Workout]:
    stmt = select(Workout).where(
        Workout.start_at >= span.start_utc, Workout.start_at < span.end_utc
    )
    if workout_type:
        stmt = stmt.where(Workout.workout_type.ilike(workout_type))
    return list(session.scalars(stmt.order_by(Workout.start_at.desc())))


@router.get("/commits", response_model=list[CommitOut])
def list_commits(
    session: SessionDep,
    span: RangeDep,
    repo: Annotated[
        str | None, Query(description="Exact full name, owner/repo.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Commit]:
    stmt = select(Commit).where(
        Commit.committed_at >= span.start_utc, Commit.committed_at < span.end_utc
    )
    if repo:
        stmt = stmt.where(Commit.repo == repo)
    stmt = stmt.order_by(Commit.committed_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt))


@router.get("/books", response_model=list[BookOut])
def list_books(
    session: SessionDep,
    book_status: Annotated[BookStatus | None, Query(alias="status")] = None,
) -> list[Book]:
    stmt = select(Book)
    if book_status:
        stmt = stmt.where(Book.status == book_status)
    stmt = stmt.order_by(Book.finished_on.desc().nullslast(), Book.title)
    return list(session.scalars(stmt))


@router.get("/days/{day}", response_model=DayRollup)
def get_day(
    session: SessionDep,
    day: Annotated[date, Path(description="Local calendar date, YYYY-MM-DD.")],
) -> DayRollup:
    start_utc, end_utc = local_day_bounds(day, local_timezone())

    sleep = session.scalars(
        select(SleepSession)
        .where(SleepSession.night_of == day)
        .order_by(SleepSession.source)
    ).first()

    workouts = session.scalars(
        select(Workout)
        .where(Workout.start_at >= start_utc, Workout.start_at < end_utc)
        .order_by(Workout.start_at)
    ).all()

    commits = session.scalars(
        select(Commit)
        .where(Commit.committed_at >= start_utc, Commit.committed_at < end_utc)
        .order_by(Commit.committed_at)
    ).all()

    finished = session.scalars(
        select(Book).where(Book.finished_on == day).order_by(Book.title)
    ).all()

    reading = session.scalars(
        select(Book).where(Book.status == "reading").order_by(Book.title)
    ).all()

    return DayRollup(
        date=day,
        sleep=SleepSessionOut.model_validate(sleep) if sleep else None,
        workouts=[WorkoutOut.model_validate(w) for w in workouts],
        commits=[CommitOut.model_validate(c) for c in commits],
        finished_books=[BookOut.model_validate(b) for b in finished],
        currently_reading=[BookOut.model_validate(b) for b in reading],
    )


@public_router.get("/summary", response_model=PublicSummary)
def public_summary(session: SessionDep) -> PublicSummary:
    """Coarse totals for the last week. Deliberately unauthenticated."""
    span = last_n_days(SUMMARY_DAYS, local_timezone())

    avg_minutes = session.scalar(
        select(func.avg(SleepSession.asleep_min)).where(
            SleepSession.night_of >= span.start,
            SleepSession.night_of <= span.end,
            SleepSession.asleep_min.is_not(None),
        )
    )

    workout_count = session.scalar(
        select(func.count())
        .select_from(Workout)
        .where(Workout.start_at >= span.start_utc, Workout.start_at < span.end_utc)
    )

    commit_count = session.scalar(
        select(func.count())
        .select_from(Commit)
        .where(
            Commit.committed_at >= span.start_utc, Commit.committed_at < span.end_utc
        )
    )

    titles = session.scalars(
        select(Book.title).where(Book.status == "reading").order_by(Book.title)
    ).all()

    return PublicSummary(
        start=span.start,
        end=span.end,
        days=span.days,
        avg_sleep_hours=round(float(avg_minutes) / 60, 1) if avg_minutes else None,
        workout_count=workout_count or 0,
        commit_count=commit_count or 0,
        currently_reading=list(titles),
    )
