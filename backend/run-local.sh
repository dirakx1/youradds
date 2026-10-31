#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# if ! pg_ctl -D data/postgres status >/dev/null 2>&1; then
#   pg_ctl -D data/postgres -l data/postgres.log start -w
# fi

.venv/bin/python -m alembic upgrade head
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 "$@"
