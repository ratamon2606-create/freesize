#!/usr/bin/env bash
# Run manually ON THE DEDICATED VPS, after copying deployment files and offline model.
set -Eeuo pipefail
umask 077
cd /opt/wardrobeshare
bash ./verify-config.sh
mkdir -p state backups
chmod 700 state backups
# This NEVER deletes volumes, demo data, or wipes any databases.
# Use a dedicated VPS and fresh project volumes for real accounts.
set -a; source .env; set +a
docker compose --env-file .env -f compose.production.yml pull
docker compose --env-file .env -f compose.production.yml up -d
for i in $(seq 1 40); do
  if docker compose --env-file .env -f compose.production.yml exec -T web python -c \
    'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:3000/api/ready",timeout=5)); assert d["ready"] and d["sql"]=="PostgreSQL" and d["nosql"]=="MongoDB" and d["background_ai"]=="in-process model ready"' >/dev/null 2>&1; then
     echo 'Bootstrap API check passed; verify external HTTPS and TLS certificate separately.'
     exit 0
  fi
  sleep 3
done
echo 'Bootstrap failed; check docker compose logs web and caddy' >&2
exit 1
