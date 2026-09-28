#!/bin/sh
# Container entrypoint: migrate, then serve.
set -e

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> starting uvicorn on port ${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
