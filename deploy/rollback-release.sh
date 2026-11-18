#!/usr/bin/env bash
# Manual rollback to prior verified web image; not a DB rollback.
set -Eeuo pipefail
umask 077
cd /opt/wardrobeshare
mkdir -p state
exec 9>state/deploy.lock
flock -x 9
[[ -f state/previous.release && -f state/current.release ]] || { echo 'No previous release recorded'; exit 1; }
set -a; source .env; set +a
cp state/current.release state/pre-rollback.release
source state/previous.release
TARGET_WEB=$WEB_IMAGE
TARGET_SHA=$RELEASE_COMMIT
[[ "$TARGET_WEB" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || exit 2
compose() { docker compose --env-file .env -f compose.production.yml "$@"; }
verify() {
  for n in $(seq 1 30); do
    if compose exec -T web python -c \
      'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:3000/api/ready",timeout=4)); assert d["ready"] and d["sql"]=="PostgreSQL" and d["nosql"]=="MongoDB" and d["background_ai"]=="in-process model ready"' >/dev/null 2>&1 \
      && curl -fsS --connect-timeout 4 --max-time 8 --resolve "${SITE_DOMAIN}:443:127.0.0.1" "${PUBLIC_ORIGIN}/api/ready" >/dev/null 2>&1; then return 0; fi
    sleep 3
  done
  return 1
}
write_image_env() {
 WEB_IMAGE_TO_PERSIST="$1" python3 - <<'PY'
import pathlib,os,tempfile
p=pathlib.Path('.env');v=os.environ['WEB_IMAGE_TO_PERSIST']
lines=[x for x in p.read_text().splitlines() if not x.startswith('WEB_IMAGE=')]
lines.append('WEB_IMAGE='+v)
fd,path=tempfile.mkstemp(prefix='.env-rollback-',dir='.')
try:
 os.chmod(path,0o600)
 with os.fdopen(fd,'w') as f:f.write('\n'.join(lines)+'\n');f.flush();os.fsync(f.fileno())
 os.replace(path,p)
finally:
 if os.path.exists(path):os.unlink(path)
PY
}
export WEB_IMAGE="$TARGET_WEB"
compose pull web
compose up -d --no-deps web
if verify; then
    mv state/pre-rollback.release state/previous.release
    printf 'WEB_IMAGE=%q\nRELEASE_COMMIT=%q\n' "$TARGET_WEB" "$TARGET_SHA" > state/current.release
    write_image_env "$TARGET_WEB"
    echo "MANUAL_ROLLBACK_VERIFIED commit=$TARGET_SHA"
else
    source state/pre-rollback.release
    export WEB_IMAGE
    compose up -d --no-deps web || true
    echo 'Rollback health check failed; returned to pre-rollback image (manual investigation required).' >&2
    exit 1
fi
