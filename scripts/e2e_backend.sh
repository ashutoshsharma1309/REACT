#!/usr/bin/env bash
# Start a REACT backend on an isolated database for the Playwright end-to-end tests.
# Creates the database if needed, migrates it and reseeds it with the REACT Merchant demo business on every run, so
# each run starts from the same deterministic data. Never touches the dev database.
set -euo pipefail
cd "$(dirname "$0")/.."
DB_NAME="${E2E_DB_NAME:-react_e2e}"
PORT="${E2E_API_PORT:-8010}"
BASE_URL="${E2E_DATABASE_BASE:-postgresql+psycopg://localhost:5432}"
psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || psql -d postgres -qc "CREATE DATABASE ${DB_NAME};"
export DATABASE_URL="${BASE_URL}/${DB_NAME}"
export AUTO_CREATE_SCHEMA=false SEED_ON_STARTUP=false REACT_DOMAIN=merchant HISTORY_DAYS="${E2E_HISTORY_DAYS:-120}" LLM_PROVIDER=deterministic LOG_LEVEL=WARNING
.venv/bin/alembic upgrade head >/dev/null
.venv/bin/python -m scripts.seed --domain merchant >/dev/null
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
