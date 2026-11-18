#!/usr/bin/env bash
# Consistent *per-database* dumps + uploads (not a single distributed point-in-time snapshot).
set -Eeuo pipefail
umask 077
cd /opt/wardrobeshare
set -a; source .env; set +a
mkdir -p backups
chmod 700 backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
compose() { docker compose --env-file .env -f compose.production.yml "$@"; }
# Avoid reporting success when an individual dump or archive failed.
compose exec -T postgres pg_dump -U wardrobe -d wardrobeshare | gzip > "backups/postgres-$stamp.sql.gz"
compose exec -T mongodb sh -c \
  'mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --db wardrobeshare --archive --gzip' \
  > "backups/mongodb-$stamp.archive.gz"
compose exec -T web tar -C /app/data -czf - uploads > "backups/uploads-$stamp.tar.gz"
for file in "backups/postgres-$stamp.sql.gz" "backups/mongodb-$stamp.archive.gz"; do gzip -t "$file"; done
tar -tzf "backups/uploads-$stamp.tar.gz" >/dev/null
sha256sum "backups/postgres-$stamp.sql.gz" "backups/mongodb-$stamp.archive.gz" "backups/uploads-$stamp.tar.gz" > "backups/SHA256SUMS-$stamp.txt"
chmod 600 "backups/"*"$stamp"*
echo "BACKUP_SUCCESS $stamp. Copy securely OFF the VPS; local files alone are not disaster recovery."
