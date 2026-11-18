#!/usr/bin/env python3
"""Safely add a GHCR-success -> Tailscale SSH trigger to Freesize release.yml.

Run this from the Freesize git repository root on a new feature branch.
Existing CI, publish, production SSH path, Docker, database, and local timer remain unchanged.
"""
from __future__ import annotations
import pathlib
import re
import sys

REPO = pathlib.Path.cwd()
RELEASE = REPO / '.github/workflows/release.yml'
ANCHOR = '  production:\n'
MARKER = '  notify_local:\n'

NOTIFY = r'''  notify_local:
    name: Trigger private laptop deploy after GHCR publish
    needs: publish
    if: ${{ needs.publish.result == 'success' && vars.FREESIZE_PUSH_DEPLOY_ENABLED == 'true' }}
    runs-on: ubuntu-latest
    timeout-minutes: 15
    # Notification failures must not invalidate a published GHCR release; the 30-minute timer is fallback.
    continue-on-error: true
    permissions:
      contents: read
    steps:
      - name: Connect to Freesize tailnet (ephemeral runner)
        uses: tailscale/github-action@v4
        with:
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          tags: tag:freesize-ci
          ping: ${{ vars.FREESIZE_TAILSCALE_IP }}
      - name: Request restricted laptop deploy
        env:
          SSH_PRIVATE_KEY: ${{ secrets.FREESIZE_SSH_PRIVATE_KEY }}
          SSH_KNOWN_HOSTS: ${{ secrets.FREESIZE_SSH_KNOWN_HOSTS }}
          TAILSCALE_IP: ${{ vars.FREESIZE_TAILSCALE_IP }}
          EXPECTED_SHA: ${{ needs.publish.outputs.commit }}
        shell: bash
        run: |
          set -Eeuo pipefail
          [[ "$TAILSCALE_IP" =~ ^100\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Configure FREESIZE_TAILSCALE_IP as the Ubuntu tailnet IPv4 address'; exit 2; }
          [[ "$EXPECTED_SHA" =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid published commit SHA'; exit 2; }
          [[ -n "$SSH_PRIVATE_KEY" && -n "$SSH_KNOWN_HOSTS" ]] || { echo 'Configure restricted SSH secrets'; exit 2; }
          install -d -m 0700 "$RUNNER_TEMP/freesize-ssh"
          printf '%s\n' "$SSH_PRIVATE_KEY" > "$RUNNER_TEMP/freesize-ssh/id_ed25519"
          printf '%s\n' "$SSH_KNOWN_HOSTS" > "$RUNNER_TEMP/freesize-ssh/known_hosts"
          chmod 0600 "$RUNNER_TEMP/freesize-ssh/id_ed25519" "$RUNNER_TEMP/freesize-ssh/known_hosts"
          # Fail closed if the server's pinned SSH host key differs.
          timeout 570 ssh -4 -F /dev/null -T \
            -i "$RUNNER_TEMP/freesize-ssh/id_ed25519" -p 2222 \
            -o BatchMode=yes -o IdentitiesOnly=yes \
            -o StrictHostKeyChecking=yes \
            -o UserKnownHostsFile="$RUNNER_TEMP/freesize-ssh/known_hosts" \
            -o ConnectTimeout=20 \
            "freesize-trigger@$TAILSCALE_IP" "deploy $EXPECTED_SHA"
          echo "Laptop deploy trigger completed for ${EXPECTED_SHA:0:12}"

'''

def main() -> None:
    if not RELEASE.is_file():
        raise SystemExit('STOP: Cannot find .github/workflows/release.yml; run inside Freesize repo root.')
    content = RELEASE.read_text(encoding='utf-8')
    if MARKER in content:
        print('NO CHANGE: notify_local job already exists; review it manually.')
        return
    checks = [
        ('expected publish job', '    name: Publish immutable GHCR images'),
        ('existing main CI gate', "github.event.workflow_run.conclusion == 'success'"),
        ('immutable commit output', 'commit: ${{ steps.meta.outputs.commit }}'),
        ('prod boundary', ANCHOR),
        ('publish job name', '  publish:\n'),
    ]
    for label, needle in checks:
        if needle not in content:
            raise SystemExit(f'STOP: Missing {label}; release workflow changed; no edits made.')
    if content.count(ANCHOR) != 1:
        raise SystemExit('STOP: Multiple/no production job boundaries; no edits made.')
    result = content.replace(ANCHOR, NOTIFY + ANCHOR, 1)
    RELEASE.write_text(result, encoding='utf-8')
    print('SUCCESS: Added notify_local job to release.yml (disabled until FREESIZE_PUSH_DEPLOY_ENABLED=true).')
    print('NEXT: git diff --check && git diff .github/workflows/release.yml')
    print('IMPORTANT: Configure tailnet OAuth, limited SSH and host key before enabling push trigger.')

if __name__ == '__main__':
    main()
