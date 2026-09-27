from datetime import date, datetime
from typing import Any, ClassVar, Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

BookStatus = Literal["want_to_read", "reading", "read", "paused", "dnf"]
BOOK_STATUSES: tuple[BookStatus, ...] = (
    "want_to_read",
    "reading",
    "read",
    "paused",
    "dnf",
)

SyncStatus = Literal["running", "success", "error"]
SYNC_STATUSES: tuple[SyncStatus, ...] = ("running", "success", "error")

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _in_values(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


class SourceRecordMixin:
    """Columns shared by every table that stores records pulled from a source."""

    # Name of the column that gets a plain btree index, set by each table.
    __time_column__: ClassVar[str | None] = None
    # Extra constraints/indexes a table wants on top of the mixin's own.
    __extra_table_args__: ClassVar[tuple[Any, ...]] = ()

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        args: list[Any] = [
            UniqueConstraint(
                "source",
                "external_id",
                name=f"uq_{cls.__tablename__}_source_external_id",
            )
        ]
        if cls.__time_column__ is not None:
            args.append(
                Index(
                    f"ix_{cls.__tablename__}_{cls.__time_column__}",
                    cls.__time_column__,
                )
            )
        args.extend(cls.__extra_table_args__)
        return tuple(args)


class SleepSession(SourceRecordMixin, Base):
    __tablename__ = "sleep_sessions"
    __time_column__ = "night_of"

    night_of: Mapped[date] = mapped_column(Date, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asleep_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_bed_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    core_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    deep_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    rem_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    awake_min: Mapped[float | None] = mapped_column(Float, nullable=True)


class Workout(SourceRecordMixin, Base):
    __tablename__ = "workouts"
    __time_column__ = "start_at"

    workout_type: Mapped[str] = mapped_column(String(64), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    active_kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)


class Commit(SourceRecordMixin, Base):
    __tablename__ = "commits"
    __time_column__ = "committed_at"

    repo: Mapped[str] = mapped_column(String(255), nullable=False)
    sha: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    additions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deletions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)


class Book(SourceRecordMixin, Base):
    __tablename__ = "books"
    __time_column__ = "finished_on"
    __extra_table_args__ = (
        CheckConstraint(_in_values("status", BOOK_STATUSES), name="status_valid"),
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[BookStatus] = mapped_column(String(32), nullable=False)
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    finished_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(_in_values("status", SYNC_STATUSES), name="status_valid"),
        Index("ix_sync_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[SyncStatus] = mapped_column(String(16), nullable=False)
    records_upserted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
