#!/usr/bin/env bash
# Run from the configured production VM. Backup files stay on your VM; copy them off-host securely.
set -Eeuo pipefail
umask 077
cd /opt/wardrobeshare
source .env
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
# SQL dump represents a consistent snapshot within PostgreSQL.
docker compose -f compose.production.yml exec -T postgres pg_dump -U wardrobe -d wardrobeshare \
  | gzip > "backups/postgres-$stamp.sql.gz"
# Mongo uses its separate snapshot; coordinate a maintenance window for whole-app restoration.
docker compose -f compose.production.yml exec -T mongodb sh -c \
  'mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db wardrobeshare --archive --gzip' \
  > "backups/mongodb-$stamp.archive.gz"
# App images are ephemeral. Uploaded photos are persistent and must also be backed up.
docker compose -f compose.production.yml exec -T web tar -C /app/data -czf - uploads \
  > "backups/uploads-$stamp.tar.gz"
echo "Backups saved to /opt/wardrobeshare/backups (UTC stamp $stamp)"
