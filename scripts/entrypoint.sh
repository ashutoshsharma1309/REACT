#!/bin/sh
# Container entrypoint (local compose and ECS).
#   RUN_MIGRATIONS=true   run "alembic upgrade head" first (serialized across tasks by a PostgreSQL advisory lock)
#   WEB_CONCURRENCY       uvicorn worker processes (default 2)
# Nothing here prints environment variables: DB_PASSWORD and other secrets never reach the logs.
set -eu
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo '{"event":"startup.migrations","status":"running"}'
  alembic upgrade head
fi
exec uvicorn app.main:app \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers --forwarded-allow-ips "*" \
  --timeout-keep-alive 75 \
  --no-server-header
