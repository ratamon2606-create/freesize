#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd /opt/wardrobeshare
exec 9>state/deploy.lock
flock -x 9
[[ -f state/previous.release && -f state/current.release ]] || { echo 'No previous release stored';exit 1; }
source .env
cp state/current.release state/pre-rollback.release
source state/previous.release
export WEB_IMAGE
compose() { docker compose -f compose.production.yml "$@"; }
compose pull web
compose up -d --no-deps web
for n in $(seq 1 30); do
  if curl --fail --silent --max-time 4 http://127.0.0.1:3000/api/ready \
    | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x["ready"] and x["sql"]=="PostgreSQL" and x["nosql"]=="MongoDB"' >/dev/null 2>&1; then
    mv state/pre-rollback.release state/previous.release
    printf 'WEB_IMAGE=%q\nRELEASE_COMMIT=%q\n' "$WEB_IMAGE" "$RELEASE_COMMIT" > state/current.release
    echo "Restored previous application release $RELEASE_COMMIT"
    exit 0
  fi
  sleep 3
done
# Attempt to return to the image that was live when rollback began.
source state/pre-rollback.release
export WEB_IMAGE
compose up -d --no-deps web || true
echo 'Rollback did not pass health check. Operator intervention needed.' >&2
exit 1
