#!/usr/bin/env bash
# Run as tcrose (not sudo) from the laptop's freesize repo.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(id -un)"
STATE="$HOME/.local/state/freesize-local-deploy"
[[ "$USER_NAME" != root ]] || { echo 'Run this script as your normal Ubuntu user, not root'; exit 2; }
[[ -f "$ROOT/.env.friends" && -f "$ROOT/compose.friends.yml" && -f "$ROOT/docker-compose.yml" ]] || { echo 'Missing existing freesize-friends Compose files; not installing.'; exit 2; }
command -v docker >/dev/null && docker info >/dev/null || { echo 'Current user cannot access Docker'; exit 2; }
python3 -m py_compile "$ROOT/scripts/freesize_local_deploy.py"
mkdir -p "$STATE"; chmod 700 "$STATE"
# A fresh install never deploys anything automatically.
touch "$STATE/PAUSED"; chmod 600 "$STATE/PAUSED"
rm -f "$STATE/ENABLED"
svc="$(mktemp)"; tmr="$(mktemp)"
trap 'rm -f "$svc" "$tmr"' EXIT
cat > "$svc" <<EOF
[Unit]
Description=Freesize local CI-gated Docker auto-deploy with health/rollback
After=docker.service network-online.target tailscaled.service
Wants=network-online.target
[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$ROOT
ExecStart=/usr/bin/python3 $ROOT/scripts/freesize_local_deploy.py check
TimeoutStartSec=600
EOF
cat > "$tmr" <<'EOF'
[Unit]
Description=Check Freesize verified releases every 30 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
AccuracySec=1min
Unit=freesize-local-deploy.service
[Install]
WantedBy=timers.target
EOF
sudo install -m 644 "$svc" /etc/systemd/system/freesize-local-deploy.service
sudo install -m 644 "$tmr" /etc/systemd/system/freesize-local-deploy.timer
sudo systemctl daemon-reload
sudo systemctl enable --now freesize-local-deploy.timer
echo "Installed and PAUSED. Existing web and DB were NOT changed."
echo "Status: python3 scripts/freesize_local_deploy.py status"
echo "Timer : systemctl list-timers freesize-local-deploy.timer"
