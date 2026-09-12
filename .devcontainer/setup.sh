#!/usr/bin/env bash
# One-time setup for a Codespace or any devcontainer.
#
# Installs the backend into a project-local virtualenv and the frontend's npm
# dependencies, then generates the test fixtures so the app has something to
# open immediately.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Python environment"
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r backend/requirements-dev.txt

echo "==> Frontend dependencies"
(cd frontend && npm ci --no-audit --no-fund)

echo "==> Test fixtures"
.venv/bin/python scripts/make_test_data.py test-data

echo
echo "Setup complete."
echo "  ./scripts/dev.sh                     start API + web UI"
echo "  docker compose up --build            start the production containers"
echo "  cd backend && python -m pytest -q    run the test suite"
