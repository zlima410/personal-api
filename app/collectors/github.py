import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Commit, SyncRun
from app.upsert import upsert_rows

logger = logging.getLogger(__name__)

SOURCE = "github"
API_ROOT = "https://api.github.com"
PER_PAGE = 100
BACKFILL_DAYS = 365
MESSAGE_MAX_CHARS = 500
RATE_LIMIT_WARN_AT = 100
TIMEOUT_SECONDS = 30.0

_LINK_RE = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"')


# --- HTTP ---------------------------------------------------------------


def build_client(token: str | None) -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API_ROOT, headers=headers, timeout=TIMEOUT_SECONDS)


def parse_link_header(value: str | None) -> dict[str, str]:
    """Turn a GitHub Link header into a {rel: url} mapping."""
    if not value:
        return {}
    return {match["rel"]: match["url"] for match in _LINK_RE.finditer(value)}


def get(
    client: httpx.Client, url: str, params: dict[str, Any] | None = None
) -> httpx.Response:
    """Single GET. Every request in this module goes through here."""
    response = client.get(url, params=params)
    warn_on_rate_limit(response)
    return response


def warn_on_rate_limit(response: httpx.Response) -> None:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return
    try:
        count = int(remaining)
    except ValueError:
        return
    if count < RATE_LIMIT_WARN_AT:
        logger.warning(
            "GitHub rate limit low: %s requests remaining, resets at %s",
            count,
            response.headers.get("X-RateLimit-Reset", "unknown"),
        )


def iter_pages(
    client: httpx.Client, url: str, params: dict[str, Any] | None = None
) -> Iterator[list[dict[str, Any]]]:
    """Yield each page of a paginated collection, following rel="next"."""
    next_url: str | None = url
    # Params only apply to the first request; the rel="next" URL carries its own.
    next_params = params

    while next_url:
        response = get(client, next_url, next_params)
        response.raise_for_status()
        yield response.json()
        next_url = parse_link_header(response.headers.get("Link")).get("next")
        next_params = None


def list_repos(client: httpx.Client) -> Iterator[dict[str, Any]]:
    """Yield every repo owned by the authenticated user, minus forks and archives."""
    params = {"affiliation": "owner", "per_page": PER_PAGE, "sort": "full_name"}
    for page in iter_pages(client, "/user/repos", params):
        for repo in page:
            if repo.get("fork") or repo.get("archived"):
                continue
            yield repo


def iter_repo_commits(
    client: httpx.Client, full_name: str, author: str, since: datetime
) -> Iterator[list[dict[str, Any]]]:
    """Yield each page of the author's commits in one repo."""
    params = {
        "author": author,
        "since": since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "per_page": PER_PAGE,
    }
    try:
        yield from iter_pages(client, f"/repos/{full_name}/commits", params)
    except httpx.HTTPStatusError as exc:
        # An empty repository has no commits to list and answers 409.
        if exc.response.status_code != 409:
            raise
        logger.debug("%s is empty, skipping", full_name)


# --- Mapping ------------------------------------------------------------


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def commit_to_row(commit: dict[str, Any], full_name: str) -> dict[str, Any]:
    message = commit["commit"]["message"] or ""
    return {
        "source": SOURCE,
        "external_id": commit["sha"],
        "repo": full_name,
        "sha": commit["sha"],
        "message": message[:MESSAGE_MAX_CHARS],
        "committed_at": parse_timestamp(commit["commit"]["author"]["date"]),
        "additions": None,
        "deletions": None,
        "url": commit["html_url"],
        "raw": commit,
    }


# --- Sync ---------------------------------------------------------------


def latest_cursor(session: Session) -> datetime | None:
    """The cursor left behind by the most recent successful github run."""
    raw = session.scalars(
        select(SyncRun.cursor)
        .where(
            SyncRun.source == SOURCE,
            SyncRun.status == "success",
            SyncRun.cursor.is_not(None),
        )
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()
    return parse_timestamp(raw) if raw else None


def sync_github(session: Session) -> int:
    """Pull the user's commits into the commits table. Returns rows upserted."""
    settings = get_settings()
    run = SyncRun(source=SOURCE, status="running", started_at=datetime.now(UTC))
    session.add(run)
    session.commit()

    try:
        cursor = latest_cursor(session)
        since = cursor or datetime.now(UTC) - timedelta(days=BACKFILL_DAYS)
        logger.info("syncing github commits since %s", since.isoformat())

        upserted = 0
        newest = cursor

        with build_client(settings.github_token) as client:
            for repo in list_repos(client):
                full_name = repo["full_name"]
                for page in iter_repo_commits(
                    client, full_name, settings.github_username, since
                ):
                    if not page:
                        continue
                    rows = [commit_to_row(commit, full_name) for commit in page]
                    upserted += upsert_rows(session, Commit, rows)
                    page_newest = max(row["committed_at"] for row in rows)
                    if newest is None or page_newest > newest:
                        newest = page_newest
                logger.debug("%s: %s commits so far", full_name, upserted)

        run.status = "success"
        run.finished_at = datetime.now(UTC)
        run.records_upserted = upserted
        run.cursor = newest.isoformat() if newest else None
        session.commit()
        logger.info("github sync upserted %s commits", upserted)
        return upserted
    except Exception as exc:
        session.rollback()
        run.status = "error"
        run.finished_at = datetime.now(UTC)
        run.error = str(exc)
        session.commit()
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from app.db import SessionLocal

    with SessionLocal() as session:
        print(sync_github(session))
