from typing import Any

from sqlalchemy import func, inspect
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Base

CONFLICT_COLUMNS = ("source", "external_id")
NEVER_UPDATED = frozenset({"id", "created_at"})


def upsert_rows(session: Session, model: type[Base], rows: list[dict[str, Any]]) -> int:
    """Insert rows, updating any that already exist for (source, external_id).

    Returns the number of rows inserted or updated. The caller owns the
    transaction; this only executes the statements.
    """
    if not rows:
        return 0

    table = inspect(model).local_table
    column_names = {column.name for column in table.columns}

    affected = 0
    for batch in _batches(rows):
        unknown = set(batch[0]) - column_names
        if unknown:
            raise ValueError(
                f"{table.name} has no column(s): {', '.join(sorted(unknown))}"
            )

        stmt = insert(table).values(batch)
        updates = {
            name: stmt.excluded[name] for name in batch[0] if name not in NEVER_UPDATED
        }
        updates["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=list(CONFLICT_COLUMNS), set_=updates
        ).returning(table.c.id)

        # psycopg reports rowcount as -1 here, so count the RETURNING rows.
        affected += len(session.execute(stmt).all())

    return affected


def _batches(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group rows so each statement has uniform columns and no repeated key.

    Postgres refuses to let ON CONFLICT DO UPDATE touch the same row twice in
    one statement, so a duplicated (source, external_id) starts a new batch and
    the later row wins.
    """
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_shape: frozenset[str] | None = None
    seen: set[tuple[Any, ...]] = set()

    for row in rows:
        shape = frozenset(row)
        key = tuple(row.get(name) for name in CONFLICT_COLUMNS)
        if current and (shape != current_shape or key in seen):
            batches.append(current)
            current = []
            seen = set()

        current.append(row)
        current_shape = shape
        seen.add(key)

    if current:
        batches.append(current)
    return batches
