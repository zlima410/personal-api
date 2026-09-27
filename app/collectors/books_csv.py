import csv
import logging
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import BOOK_STATUSES, Book
from app.upsert import upsert_rows

logger = logging.getLogger(__name__)

SOURCE = "manual"
REQUIRED_COLUMNS = {"title", "authors", "status"}
AUTHOR_SEPARATOR = ";"

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase ASCII slug, so 'Les Misérables' and 'Les Miserables' agree."""
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    return _SLUG_STRIP_RE.sub("-", ascii_value.lower()).strip("-")


def make_external_id(title: str, authors: list[str]) -> str:
    first_author = authors[0] if authors else ""
    slug = slugify(f"{title} {first_author}")
    return slug or slugify(title)


def parse_authors(value: str | None) -> list[str]:
    if not value:
        return []
    names = []
    for part in value.split(AUTHOR_SEPARATOR):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


def parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def parse_rating(value: str | None) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def row_to_book(row: dict[str, str], line: int) -> dict[str, Any]:
    """Validate one CSV row and map it to a Book row. Raises ValueError on bad input."""
    title = (row.get("title") or "").strip()
    if not title:
        raise ValueError(f"line {line}: title is required")

    status = (row.get("status") or "").strip().lower()
    if status not in BOOK_STATUSES:
        allowed = ", ".join(BOOK_STATUSES)
        raise ValueError(f"line {line}: status {status!r} must be one of: {allowed}")

    authors = parse_authors(row.get("authors"))

    try:
        started_on = parse_date(row.get("started_on"))
        finished_on = parse_date(row.get("finished_on"))
        rating = parse_rating(row.get("rating"))
    except ValueError as exc:
        raise ValueError(f"line {line}: {exc}") from exc

    return {
        "source": SOURCE,
        "external_id": make_external_id(title, authors),
        "title": title,
        "authors": authors,
        "status": status,
        "started_on": started_on,
        "finished_on": finished_on,
        "rating": rating,
        "raw": dict(row),
    }


def import_books_csv(session: Session, path: str | Path) -> int:
    """Import a books CSV into the books table. Returns rows upserted."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{path} is missing column(s): {', '.join(sorted(missing))}"
            )

        rows = [
            # Line 1 is the header, so data starts at 2.
            row_to_book(row, line)
            for line, row in enumerate(reader, start=2)
        ]

    if not rows:
        logger.info("%s has no data rows", path)
        return 0

    upserted = upsert_rows(session, Book, rows)
    session.commit()
    logger.info("imported %s books from %s", upserted, path)
    return upserted


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.collectors.books_csv <path-to-csv>")

    from app.db import SessionLocal

    with SessionLocal() as session:
        print(import_books_csv(session, sys.argv[1]))
