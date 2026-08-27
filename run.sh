#!/usr/bin/env bash
# Local dev server. Creates a venv on first run, then hot-reloads.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if [ ! -d .venv ]; then
  echo "→ creating .venv"
  "$PY" -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements-dev.txt
fi

if [ ! -f .env ]; then
  echo "→ no .env found; starting in DEMO MODE (scripted opponent, no API key needed)."
  echo "  cp .env.example .env  and fill in a key to play against a real model."
fi

PORT=${PORT:-8080}
echo "→ http://127.0.0.1:${PORT}"
exec ./.venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port "${PORT}" --reload
