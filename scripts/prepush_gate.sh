#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
command -v docker >/dev/null || { echo 'Docker not found. Run gate on your Ubuntu laptop with Docker.' >&2; exit 2; }
docker compose version >/dev/null || { echo 'Docker Compose plugin missing.' >&2; exit 2; }
python3 -m compileall -q app.py tests
python3 scripts/requirements_static_audit.py
node --check static/js/app.js
for script in deploy/*.sh; do bash -n "$script"; done
mkdir -p docs/screenshots/evidence
cleanup() { docker compose -f docker-compose.gate.yml -p wardrobeshare-prepush down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT
# This separate project name ensures down -v touches only disposable GATE volumes.
docker compose -f docker-compose.gate.yml -p wardrobeshare-prepush up --abort-on-container-exit --exit-code-from gate
# Build the same production image deployed by CI, including local in-process AI runtime.
docker build -t wardrobeshare-prepush:verified .
echo 'PREPUSH GATE PASSED: preview tests + PostgreSQL/MongoDB tests + production Docker build.'
echo 'Next: capture real UI and database screenshots, then open a PR (do not push directly to main).'
