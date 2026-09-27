import logging
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Book, SyncRun
from app.upsert import upsert_rows

logger = logging.getLogger(__name__)

SOURCE = "hardcover"
API_URL = "https://api.hardcover.app/v1/graphql"
USER_AGENT = "personal-api/0.1"
PAGE_SIZE = 100
TIMEOUT_SECONDS = 30.0
DEFAULT_RETRY_AFTER = 60
MAX_RETRY_AFTER = 300

# Hardcover's status_id values. 6 is "removed" and has no place in the library.
STATUS_BY_ID: dict[int, str] = {
    1: "want_to_read",
    2: "reading",
    3: "read",
    4: "paused",
    5: "dnf",
}

# Hardcover rejects requests with more than one root field, so this asks for
# user_books and nothing else.
USER_BOOKS_QUERY = """
query UserBooks($userId: Int!, $limit: Int!, $offset: Int!) {
  user_books(
    where: { user_id: { _eq: $userId } }
    order_by: { id: asc }
    limit: $limit
    offset: $offset
  ) {
    id
    status_id
    rating
    first_started_reading_date
    last_read_date
    updated_at
    book {
      title
      image {
        url
      }
      contributions {
        author {
          name
        }
      }
    }
  }
}
"""


class GraphQLError(RuntimeError):
    """The server answered 200 but the response carried an errors array."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        messages = "; ".join(e.get("message", repr(e)) for e in errors)
        super().__init__(f"Hardcover GraphQL error: {messages}")


# --- HTTP ---------------------------------------------------------------


def build_client(token: str | None) -> httpx.Client:
    headers = {
        "content-type": "application/json",
        "user-agent": USER_AGENT,
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    return httpx.Client(headers=headers, timeout=TIMEOUT_SECONDS)


def retry_after_seconds(response: httpx.Response) -> int:
    """Seconds to wait after a 429, clamped so a bad header cannot hang the sync."""
    try:
        seconds = int(response.headers.get("Retry-After", ""))
    except ValueError:
        seconds = DEFAULT_RETRY_AFTER
    return max(1, min(seconds, MAX_RETRY_AFTER))


def post_graphql(
    client: httpx.Client, query: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """Execute one GraphQL request and return its data. The only HTTP call here."""
    payload = {"query": query, "variables": variables}
    response = client.post(API_URL, json=payload)

    if response.status_code == 429:
        delay = retry_after_seconds(response)
        logger.warning("Hardcover rate limited, retrying once in %ss", delay)
        time.sleep(delay)
        response = client.post(API_URL, json=payload)

    response.raise_for_status()
    body = response.json()

    if body.get("errors"):
        raise GraphQLError(body["errors"])
    return body.get("data") or {}


def fetch_user_books(
    client: httpx.Client, user_id: int, limit: int, offset: int
) -> list[dict[str, Any]]:
    data = post_graphql(
        client,
        USER_BOOKS_QUERY,
        {"userId": user_id, "limit": limit, "offset": offset},
    )
    return data.get("user_books") or []


def iter_user_books(
    client: httpx.Client, user_id: int
) -> Iterator[list[dict[str, Any]]]:
    """Yield pages of user_books, walking offset until a short page arrives."""
    offset = 0
    while True:
        page = fetch_user_books(client, user_id, PAGE_SIZE, offset)
        if page:
            yield page
        if len(page) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


# --- Mapping ------------------------------------------------------------


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def extract_authors(book: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for contribution in book.get("contributions") or []:
        author = contribution.get("author") or {}
        name = (author.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def user_book_to_row(user_book: dict[str, Any]) -> dict[str, Any] | None:
    """Map one user_book to a Book row, or None if it should be skipped."""
    status = STATUS_BY_ID.get(user_book.get("status_id"))
    if status is None:
        return None

    book = user_book.get("book") or {}
    title = (book.get("title") or "").strip()
    if not title:
        logger.warning("user_book %s has no title, skipping", user_book.get("id"))
        return None

    rating = user_book.get("rating")
    image = book.get("image") or {}

    return {
        "source": SOURCE,
        "external_id": str(user_book["id"]),
        "title": title,
        "authors": extract_authors(book),
        "status": status,
        "started_on": parse_date(user_book.get("first_started_reading_date")),
        "finished_on": parse_date(user_book.get("last_read_date")),
        "rating": float(rating) if rating is not None else None,
        "cover_url": image.get("url"),
        "raw": user_book,
    }


# --- Sync ---------------------------------------------------------------


def sync_hardcover(session: Session) -> int:
    """Pull the Hardcover library into the books table. Returns rows upserted."""
    settings = get_settings()
    run = SyncRun(source=SOURCE, status="running", started_at=datetime.now(UTC))
    session.add(run)
    session.commit()

    try:
        if settings.hardcover_user_id is None:
            raise RuntimeError("HARDCOVER_USER_ID is not set")

        upserted = 0
        skipped = 0
        newest: datetime | None = None

        with build_client(settings.hardcover_token) as client:
            for page in iter_user_books(client, settings.hardcover_user_id):
                rows = []
                for user_book in page:
                    row = user_book_to_row(user_book)
                    if row is None:
                        skipped += 1
                        continue
                    rows.append(row)
                    updated = parse_timestamp(user_book.get("updated_at"))
                    if updated and (newest is None or updated > newest):
                        newest = updated
                if rows:
                    upserted += upsert_rows(session, Book, rows)

        run.status = "success"
        run.finished_at = datetime.now(UTC)
        run.records_upserted = upserted
        # The whole library is small enough to re-read every time; the cursor is
        # recorded for visibility rather than used to filter the next query.
        run.cursor = newest.isoformat() if newest else None
        session.commit()
        logger.info("hardcover sync upserted %s books (%s skipped)", upserted, skipped)
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
        print(sync_hardcover(session))
