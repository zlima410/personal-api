import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_ingest_key
from app.collectors.health import SOURCE, parse_payload, payload_summary
from app.db import get_db
from app.models import SleepSession, SyncRun, Workout
from app.upsert import upsert_rows

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_ingest_key)],
)


class HealthIngestResult(BaseModel):
    sleep: int
    workouts: int


@router.post("/health", response_model=HealthIngestResult)
def ingest_health(
    body: dict[str, Any],
    session: Annotated[Session, Depends(get_db)],
    automation_name: Annotated[str | None, Header()] = None,
) -> HealthIngestResult:
    """Receive a Health Auto Export payload and upsert its sleep and workouts."""
    logger.info("health ingest from automation %r", automation_name or "unknown")

    if "data" not in body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Body must contain a 'data' object (Health Auto Export v2 format)",
        )

    metric_names, workouts_received = payload_summary(body)
    sleep_rows, workout_rows = parse_payload(body)
    logger.info(
        "received metrics %s and %s workout(s); parsed %s sleep row(s), %s workout(s)",
        metric_names or "[]",
        workouts_received,
        len(sleep_rows),
        len(workout_rows),
    )

    run = SyncRun(source=SOURCE, status="running", started_at=datetime.now(UTC))
    session.add(run)
    session.commit()

    try:
        sleep_count = upsert_rows(session, SleepSession, sleep_rows)
        workout_count = upsert_rows(session, Workout, workout_rows)

        run.status = "success"
        run.finished_at = datetime.now(UTC)
        run.records_upserted = sleep_count + workout_count
        session.commit()
    except Exception as exc:
        session.rollback()
        run.status = "error"
        run.finished_at = datetime.now(UTC)
        run.error = str(exc)
        session.commit()
        raise

    logger.info("ingested %s sleep rows and %s workouts", sleep_count, workout_count)
    return HealthIngestResult(sleep=sleep_count, workouts=workout_count)
