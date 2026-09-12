#!/usr/bin/env bash
# Start the API and the web UI together for local development.
#
# The Vite dev server proxies /api to the backend, so the browser only ever
# talks to one origin - which is also why this works unchanged behind a
# Codespaces or SSH port forward.
#
# Usage: ./scripts/dev.sh [--host 0.0.0.0]
set -euo pipefail

cd "$(dirname "$0")/.."

HOST="127.0.0.1"
if [[ "${1:-}" == "--host" && -n "${2:-}" ]]; then
  HOST="$2"
fi

PYTHON=".venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "No .venv found. Run:"
  echo "  python3.12 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt"
  exit 1
fi
if [[ ! -d frontend/node_modules ]]; then
  echo "Frontend dependencies missing. Run: (cd frontend && npm install)"
  exit 1
fi

# Stop both halves when this script is interrupted.
pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "==> API on http://${HOST}:8000  (docs at /docs)"
(cd backend && PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --host "$HOST" --port 8000) &
pids+=($!)

# Wait for the API before starting the UI, so the first page load already
# finds the material and printer catalogue.
for _ in $(seq 1 60); do
  if "$PYTHON" - "$HOST" <<'EOF' 2>/dev/null
import sys, urllib.request
urllib.request.urlopen(f"http://{sys.argv[1]}:8000/health", timeout=1)
EOF
  then break; fi
  sleep 0.5
done

echo "==> Web UI on http://${HOST}:5173"
(cd frontend && npm run dev -- --host "$HOST") &
pids+=($!)

wait -n
