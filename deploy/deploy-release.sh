#!/usr/bin/env bash
# Executed as: bash -s -- GHCR_WEB_DIGEST COMMIT_SHA
# Auto-rollback of application images on failed readiness check.
set -Eeuo pipefail
umask 077
ROOT=/opt/wardrobeshare
NEW_WEB=${1:?web image required}
COMMIT=${2:?commit required}

[[ "$NEW_WEB" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || { echo 'Invalid web digest'; exit 2; }
[[ "$COMMIT" =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid commit SHA'; exit 2; }
cd "$ROOT"
test -f .env && test -f compose.production.yml
mkdir -p state
exec 9>state/deploy.lock
flock -x 9
# Production must have been bootstrapped at least once (DB services).
source .env
: "${POSTGRES_PASSWORD:?}" "${MONGO_ROOT_PASSWORD:?}"
OLD_WEB=''; OLD_COMMIT=''
if [[ -f state/current.release ]]; then
  source state/current.release
  OLD_WEB=${WEB_IMAGE:-}; OLD_COMMIT=${RELEASE_COMMIT:-}
fi

compose() { docker compose -f compose.production.yml "$@"; }
ready() {
  for i in $(seq 1 30); do
    if curl --fail --silent --max-time 4 'http://127.0.0.1:3000/api/ready' \
      | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x["ready"] and x["sql"]=="PostgreSQL" and x["nosql"]=="MongoDB"' >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}
rollback_on_error() {
  local exit_status=$?
  trap - ERR
  echo "Release failed (exit=$exit_status). Starting app-image rollback..." >&2
  if [[ -n "$OLD_WEB" ]]; then
    export WEB_IMAGE="$OLD_WEB"
    compose up -d --no-deps web || true
    if ready; then echo 'Previous release recovered.' >&2
    else echo 'CRITICAL: Previous release did not pass readiness. Restore from backups if needed.' >&2
    fi
  else
    echo 'No previously verified deployment exists. Manual recovery required.' >&2
  fi
  exit "$exit_status"
}
trap rollback_on_error ERR
export WEB_IMAGE="$NEW_WEB"
compose pull web
compose up -d --no-deps web
ready

if [[ -f state/current.release ]]; then cp state/current.release state/previous.release; fi
printf 'WEB_IMAGE=%q\nRELEASE_COMMIT=%q\n' "$NEW_WEB" "$COMMIT" > state/current.release
chmod 600 state/current.release
trap - ERR
echo "Release verified: $COMMIT"
