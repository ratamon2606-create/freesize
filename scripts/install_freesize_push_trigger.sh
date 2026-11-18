#!/usr/bin/env bash
# Install a separate, least-privilege SSH listener for Freesize GHCR publish notifications.
# Intended for a trusted Ubuntu host already running freesize-local-deploy.service.
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/install_freesize_push_trigger.sh ~/.ssh/freesize-gha-trigger.pub" >&2
  exit 2
fi
if [[ $EUID -eq 0 ]]; then
  echo "Run as the existing Freesize Ubuntu user (NOT sudo)." >&2
  exit 2
fi
OWNER="$(id -un)"
[[ $OWNER =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Unexpected Linux username' >&2; exit 2; }
PUBKEY="$(realpath "$1")"
[[ -f "$PUBKEY" ]] || { echo "Missing public key $PUBKEY" >&2; exit 2; }
[[ -f scripts/freesize_local_deploy.py ]] || { echo 'Run from Freesize repository root.' >&2; exit 2; }
[[ -x /usr/sbin/sshd ]] || { echo 'Missing /usr/sbin/sshd. Install openssh-server first.' >&2; exit 2; }
command -v tailscale >/dev/null
command -v curl >/dev/null
sudo systemctl cat freesize-local-deploy.service >/dev/null
[[ $(sudo systemctl show freesize-local-deploy.service -p User --value) == "$OWNER" ]] || {
  echo 'Existing deploy systemd service must run as the current Freesize user.' >&2; exit 2;
}

KEY_TYPE="$(awk 'NR==1{print $1}' "$PUBKEY")"
[[ "$KEY_TYPE" == 'ssh-ed25519' ]] || { echo 'Only ssh-ed25519 public keys are allowed.' >&2; exit 2; }
ssh-keygen -lf "$PUBKEY" >/dev/null
KEY_MATERIAL="$(awk 'NR==1{print $1" "$2}' "$PUBKEY")"
[[ $(wc -l < "$PUBKEY") -eq 1 ]] || { echo 'Provide exactly one SSH public key.' >&2; exit 2; }
TAIL_IP="$(tailscale ip -4 | head -n1 | tr -d '[:space:]')"
python3 - "$TAIL_IP" <<'PY'
import ipaddress, sys
ip = ipaddress.ip_address(sys.argv[1])
assert isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network('100.64.0.0/10'), 'Expected Tailscale 100.64.0.0/10 IPv4'
PY

TRIGGER_USER='freesize-trigger'
if ! id "$TRIGGER_USER" >/dev/null 2>&1; then
  sudo useradd --create-home --user-group --shell /bin/bash "$TRIGGER_USER"
fi
TRIGGER_HOME="$(getent passwd "$TRIGGER_USER" | cut -d: -f6)"
[[ "$TRIGGER_HOME" == "/home/$TRIGGER_USER" ]] || { echo 'Unexpected trigger user home; aborting.' >&2; exit 2; }
sudo install -d -m 0700 -o "$TRIGGER_USER" -g "$TRIGGER_USER" "$TRIGGER_HOME/.ssh"
AUTH_TMP="$(mktemp)"
SSHD_TMP="$(mktemp)"
SYSTEMD_TMP="$(mktemp)"
SUDO_TMP="$(mktemp)"
ENTRY_TMP="$(mktemp)"
ROOT_TMP="$(mktemp)"
trap 'rm -f "$AUTH_TMP" "$SSHD_TMP" "$SYSTEMD_TMP" "$SUDO_TMP" "$ENTRY_TMP" "$ROOT_TMP"' EXIT

printf 'restrict %s\n' "$KEY_MATERIAL" > "$AUTH_TMP"
sudo install -m 0600 -o "$TRIGGER_USER" -g "$TRIGGER_USER" "$AUTH_TMP" "$TRIGGER_HOME/.ssh/authorized_keys"

cat > "$ENTRY_TMP" <<'ENTRY'
#!/usr/bin/env bash
set -Eeuo pipefail
# sshd ForceCommand ignores arbitrary client-provided shell commands.
if [[ ! "${SSH_ORIGINAL_COMMAND:-}" =~ ^deploy[[:space:]]([a-f0-9]{40})$ ]]; then
  echo 'Only a validated deploy <40-hex-commit-sha> trigger is permitted.' >&2
  exit 2
fi
exec /usr/bin/sudo -n /usr/local/sbin/freesize-ci-trigger "${BASH_REMATCH[1]}"
ENTRY

# The trusted release trigger is a root-owned program, not a repo-editable script.
cat > "$ROOT_TMP" <<ROOTSCRIPT
#!/usr/bin/env bash
set -Eeuo pipefail
[[ \$# -eq 1 && "\$1" =~ ^[a-f0-9]{40}\$ ]] || { echo 'Invalid commit SHA'; exit 2; }
REQUESTED_SHA="\$1"
STATE_DIR='/home/$OWNER/.local/state/freesize-local-deploy'
if [[ -f "\$STATE_DIR/PAUSED" || ! -f "\$STATE_DIR/ENABLED" ]]; then
  echo 'Agent is PAUSED; refusing push-triggered deployment.' >&2
  exit 2
fi
# If main changed during release creation, do not deploy an obsolete image.
MAIN_SHA="\$(curl --fail --silent --show-error --max-time 15 \\
  -H 'Accept: application/vnd.github+json' \\
  -H 'User-Agent: freesize-ci-trigger/1' \\
  https://api.github.com/repos/ratamon2606-create/freesize/branches/main \\
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"]["sha"])')"
if [[ "\$MAIN_SHA" != "\$REQUESTED_SHA" ]]; then
  echo "Superseded release \${REQUESTED_SHA:0:12}; current main is \${MAIN_SHA:0:12}. Deferred to latest release/timer."
  exit 0
fi
/usr/bin/systemctl start freesize-local-deploy.service
ACTUAL_SHA="\$(python3 - "\$STATE_DIR/state.json" <<'PY'
import json,sys
from pathlib import Path
try:
    print(json.loads(Path(sys.argv[1]).read_text()).get('sha',''))
except (OSError, ValueError):
    print('')
PY
)"
if [[ "\$ACTUAL_SHA" != "\$REQUESTED_SHA" ]]; then
  echo "Triggered service but requested release is not deployed: expected \${REQUESTED_SHA:0:12}, recorded \${ACTUAL_SHA:0:12}. Check systemd journal." >&2
  exit 1
fi
[[ ! -f "\$STATE_DIR/PAUSED" ]] || { echo 'Agent paused after service run.' >&2; exit 1; }
echo "DEPLOY VERIFIED: \${ACTUAL_SHA:0:12}"
ROOTSCRIPT

sudo install -d -m 0755 /usr/local/libexec
sudo install -m 0755 -o root -g root "$ENTRY_TMP" /usr/local/libexec/freesize-ssh-dispatch
sudo install -m 0755 -o root -g root "$ROOT_TMP" /usr/local/sbin/freesize-ci-trigger

printf 'freesize-trigger ALL=(root) NOPASSWD: /usr/local/sbin/freesize-ci-trigger *\n' > "$SUDO_TMP"
sudo visudo -cf "$SUDO_TMP" >/dev/null
sudo install -m 0440 -o root -g root "$SUDO_TMP" /etc/sudoers.d/freesize-ci-trigger

cat > "$SSHD_TMP" <<EOFSSHD
Port 2222
ListenAddress $TAIL_IP
AddressFamily inet
HostKey /etc/ssh/ssh_host_ed25519_key
PidFile /run/freesize-push-sshd.pid
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
UsePAM yes
AllowUsers freesize-trigger
AuthorizedKeysFile .ssh/authorized_keys
DisableForwarding yes
PermitTTY no
PermitUserRC no
PermitUserEnvironment no
X11Forwarding no
ForceCommand /usr/local/libexec/freesize-ssh-dispatch
MaxAuthTries 2
MaxSessions 1
LogLevel VERBOSE
EOFSSHD
[[ -f /etc/ssh/ssh_host_ed25519_key.pub ]] || { echo 'Missing host SSH ed25519 key. Check OpenSSH installation.' >&2; exit 2; }
sudo install -m 0644 -o root -g root "$SSHD_TMP" /etc/ssh/freesize-push-sshd.conf
sudo /usr/sbin/sshd -t -f /etc/ssh/freesize-push-sshd.conf

cat > "$SYSTEMD_TMP" <<'EOFSERVICE'
[Unit]
Description=Freesize private publish-trigger SSH listener (Tailscale only)
After=tailscaled.service network-online.target
Wants=tailscaled.service network-online.target

[Service]
Type=simple
ExecStartPre=/usr/bin/install -d -m 0755 /run/sshd
ExecStart=/usr/sbin/sshd -D -e -f /etc/ssh/freesize-push-sshd.conf
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOFSERVICE
sudo install -m 0644 -o root -g root "$SYSTEMD_TMP" /etc/systemd/system/freesize-push-trigger.service
sudo systemctl daemon-reload
sudo systemctl enable --now freesize-push-trigger.service
sudo systemctl restart freesize-push-trigger.service

printf '\n===== LINUX STATUS =====\n'
sudo systemctl --no-pager --full status freesize-push-trigger.service | head -n 17 || true
printf '\n===== SET THIS GITHUB ACTIONS VARIABLE =====\n'
printf 'FREESIZE_TAILSCALE_IP=%s\n' "$TAIL_IP"
printf '\n===== SET THIS GITHUB ACTIONS SECRET (PUBLIC HOST KEY ONLY) =====\n'
awk -v host="[$TAIL_IP]:2222" 'NR==1{print host, $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
printf '\n===== VERIFY HOST FINGERPRINT LOCALLY =====\n'
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
printf '\n===== CHECK LISTENER =====\n'
sudo ss -lntp '( sport = :2222 )' || true
printf '\nDo not upload your private SSH key to this chat or commit it to Git.\n'
