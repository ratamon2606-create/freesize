#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
test -f .env || { echo 'Missing deploy/.env, copy .env.production.example and set secure values' >&2; exit 1; }
set -a
# Trusted server-only .env file, not uploaded from git. Disallow shell substitutions in user-edited env.
source .env
set +a
for name in POSTGRES_PASSWORD MONGO_ROOT_USERNAME MONGO_ROOT_PASSWORD REGISTRATION_INVITE_CODE SITE_DOMAIN PUBLIC_ORIGIN WEB_IMAGE; do
    test -n "${!name:-}" || { echo "Missing $name" >&2; exit 1; }
done
for name in POSTGRES_PASSWORD MONGO_ROOT_PASSWORD REGISTRATION_INVITE_CODE; do
    value=${!name}
    [[ "$value" =~ ^[a-fA-F0-9]{48,}$ ]] || { echo "$name must be unique random hex (48+ chars)" >&2; exit 1; }
done
[[ "$SITE_DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] && [[ "$SITE_DOMAIN" != *.example.com ]] || { echo 'Use a real SITE_DOMAIN with working DNS' >&2; exit 1; }
[[ "$PUBLIC_ORIGIN" == "https://$SITE_DOMAIN" ]] || { echo 'PUBLIC_ORIGIN must exactly equal https://SITE_DOMAIN' >&2; exit 1; }
[[ "$WEB_IMAGE" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$ ]] || { echo 'WEB_IMAGE must be pinned to a real immutable GHCR digest' >&2; exit 1; }
test -s models/u2netp.onnx || { echo 'Missing models/u2netp.onnx (local AI)' >&2; exit 1; }
command -v docker >/dev/null && docker compose version >/dev/null || { echo 'Docker Compose is required' >&2; exit 1; }
docker compose --env-file .env -f compose.production.yml config --quiet
printf 'Config checks passed. Ensure DNS, 80/443 firewall, and GHCR pull access before launch.\n'
