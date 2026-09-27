import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin_key
from app.collectors.github import sync_github
from app.collectors.hardcover import sync_hardcover
from app.db import get_db
from app.models import SyncRun
from app.schemas import SyncRunOut, SyncTriggered

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],
)

SYNC_FUNCTIONS: dict[str, Callable[[Session], int]] = {
    "github": sync_github,
    "hardcover": sync_hardcover,
}

SessionDep = Annotated[Session, Depends(get_db)]


@router.post("/sync/{source}", response_model=SyncTriggered)
def trigger_sync(
    session: SessionDep,
    source: Annotated[str, Path(description="github or hardcover")],
) -> SyncTriggered:
    """Run a collector synchronously and report how many rows it wrote."""
    sync = SYNC_FUNCTIONS.get(source)
    if sync is None:
        known = ", ".join(sorted(SYNC_FUNCTIONS))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown source {source!r}. Known sources: {known}",
        )

    logger.info("admin triggered %s sync", source)
    return SyncTriggered(source=source, upserted=sync(session))


@router.get("/sync-runs", response_model=list[SyncRunOut])
def list_sync_runs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[SyncRun]:
    stmt = select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
    return list(session.scalars(stmt))
