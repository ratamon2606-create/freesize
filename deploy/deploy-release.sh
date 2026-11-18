#!/usr/bin/env bash
# GitHub Actions runs this script over pinned SSH (stdin): bash -s -- IMAGE@DIGEST COMMIT
# Data volumes are NEVER removed. This script replaces only the web image.
set -Eeuo pipefail
umask 077
ROOT=/opt/wardrobeshare
NEW_WEB=${1:?web image digest required}
COMMIT=${2:?40-character commit required}
[[ "$NEW_WEB" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || { echo 'Invalid immutable image digest'; exit 2; }
[[ "$COMMIT" =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid git SHA'; exit 2; }
cd "$ROOT"
test -f .env && test -f compose.production.yml && test -f Caddyfile
mkdir -p state
exec 9>state/deploy.lock
flock -x 9
set -a; source .env; set +a
: "${POSTGRES_PASSWORD:?}" "${MONGO_ROOT_PASSWORD:?}" "${REGISTRATION_INVITE_CODE:?}" "${SITE_DOMAIN:?}" "${PUBLIC_ORIGIN:?}"
[[ "$PUBLIC_ORIGIN" == "https://$SITE_DOMAIN" ]] || exit 2
# Existing current.release is authoritative; first deployment falls back to the
# known bootstrap image pinned in .env for a safe first rollback.
OLD_WEB=${WEB_IMAGE:-}
OLD_COMMIT=bootstrap
if [[ -f state/current.release ]]; then
    source state/current.release
    OLD_WEB=${WEB_IMAGE:-}
    OLD_COMMIT=${RELEASE_COMMIT:-bootstrap}
fi

compose() { docker compose --env-file .env -f compose.production.yml "$@"; }
verify() {
    for n in $(seq 1 30); do
        if compose exec -T web python -c \
            'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:3000/api/ready",timeout=4)); assert d["ready"] and d["sql"]=="PostgreSQL" and d["nosql"]=="MongoDB" and d["background_ai"]=="in-process model ready"' >/dev/null 2>&1 \
          && curl -fsS --retry 1 --connect-timeout 4 --max-time 8 \
            --resolve "${SITE_DOMAIN}:443:127.0.0.1" \
            "${PUBLIC_ORIGIN}/api/ready" \
            | python3 -c 'import json,sys; x=json.load(sys.stdin); assert x["ready"]' >/dev/null 2>&1; then
            return 0
        fi
        sleep 3
    done
    return 1
}
# Only update the on-disk WEB_IMAGE after the new revision passes health checks.
write_image_env() {
    local target=$1
    WEB_IMAGE_TO_PERSIST="$target" python3 - <<'PY'
import os,pathlib,tempfile
p=pathlib.Path('.env')
lines=p.read_text().splitlines()
value=os.environ['WEB_IMAGE_TO_PERSIST']
new=[line for line in lines if not line.startswith('WEB_IMAGE=')]
new.append('WEB_IMAGE='+value)
fd,path=tempfile.mkstemp(prefix='.env-new-',dir='.')
try:
  os.chmod(path,0o600)
  with os.fdopen(fd,'w') as f:
    f.write('\n'.join(new)+'\n')
    f.flush();os.fsync(f.fileno())
  os.replace(path,p)
finally:
  if os.path.exists(path): os.unlink(path)
PY
}
rollback_on_error() {
    local status=$?
    trap - ERR
    echo "Release verification failed ($status). Restoring previous web image..." >&2
    if [[ "$OLD_WEB" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]; then
        export WEB_IMAGE="$OLD_WEB"
        compose up -d --no-deps web || true
        if verify; then echo 'Previous web image is healthy again.' >&2
        else echo 'CRITICAL: previous web image did not recover; operator intervention required.' >&2
        fi
    else
        echo 'No verified previous image; investigate the initial deployment.' >&2
    fi
    exit "$status"
}
trap rollback_on_error ERR
export WEB_IMAGE="$NEW_WEB"
compose pull web
compose up -d --no-deps web
verify
# Save a recoverable release pointer (quote safely for bash source).
if [[ -f state/current.release ]]; then
    cp state/current.release state/previous.release
elif [[ "$OLD_WEB" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]]; then
    printf 'WEB_IMAGE=%q\nRELEASE_COMMIT=%q\n' "$OLD_WEB" "$OLD_COMMIT" > state/previous.release
fi
printf 'WEB_IMAGE=%q\nRELEASE_COMMIT=%q\n' "$NEW_WEB" "$COMMIT" > state/current.release
chmod 600 state/current.release state/previous.release 2>/dev/null || true
write_image_env "$NEW_WEB"
trap - ERR
echo "PRODUCTION_RELEASE_VERIFIED commit=$COMMIT (web image only; databases preserved)"
