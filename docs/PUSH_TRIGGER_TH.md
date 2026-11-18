# Freesize — Event-driven Auto Deploy (Tailscale + restricted SSH)

## Goal

PR → required human review (1) → 5 CI checks → merge `main` → GHCR publish successful → GitHub-hosted runner joins Tailscale temporarily → SSH to private Ubuntu tailnet IP:2222 → **restricted command** starts the **existing** `freesize-local-deploy.service` → original agent verifies release/branch protection, switches **web only**, checks PostgreSQL/Mongo/HTTPS/AI, and rolls back on health failure.

**Existing 30-minute timer stays enabled** as a fallback if GitHub's SSH notify fails or the laptop is offline. The notify job uses `continue-on-error: true`, so a failed notification does not mark the *already published* GHCR release as failed (the existing agent requires the release workflow to conclude successfully). Notification job is **disabled by default** until `FREESIZE_PUSH_DEPLOY_ENABLED=true` is configured after all other steps.

## Important safety design

- We do NOT install a self-hosted GitHub runner on a public-repository machine.
- We do NOT open public Internet access to SSH. The isolated SSH daemon listens only on the laptop's **Tailscale IPv4** at TCP port **2222**, not on 0.0.0.0.
- A dedicated Linux account `freesize-trigger` cannot open a general remote shell, forward ports, use a TTY, or run arbitrary commands. SSHD `ForceCommand` validates `deploy <40-hex-SHA>`. A root-owned trigger verifies current `main`, starts the existing systemd deploy service, and checks the recorded deployed SHA matches the published SHA. `sudoers` allows only this root-owned trigger.
- No GitHub Actions workflow secret is copied to the repo, chat, or web app. The single-use GitHub runner joins tailnet with OAuth identity `tag:freesize-ci`.
- The old local agent still requires an Active branch ruleset with a PR, at least 1 review, all 5 checks, and latest `main` verified GHCR image. Signed commits are NOT required.
- Nothing in this pack touches PostgreSQL, MongoDB, uploads, existing web container, Docker data-root, or other Docker Compose projects. Installing the listener does not deploy code.
- On GitHub job failure, keep timer as fallback. Failed/paused agent must be investigated rather than automatically `resume`-ing.

## Step A — PR changes (safe first)

1. On Ubuntu, in the cloned `~/Downloads/freesize-deploy` repo, run `git status --short`; ensure no uncommitted work and start a new branch from up-to-date `main`.
2. Copy `apply_push_deploy.py` and `scripts/install_freesize_push_trigger.sh` and `docs/PUSH_TRIGGER_TH.md` into the repo in their named locations. The patch script appends the `notify_local` job to `.github/workflows/release.yml` without changing existing publishing or Production SSH deployment.
3. Run `python3 apply_push_deploy.py`, then `bash -n scripts/install_freesize_push_trigger.sh`, `git diff --check` and `git status --short`.
4. Commit these changes and push feature branch; open PR, obtain 1 approval and 5 CI checks, and merge.
5. After merge, `git switch main && git pull --ff-only origin main`.
6. Since `FREESIZE_PUSH_DEPLOY_ENABLED` is not set, the new GitHub job is skipped until Step D is complete.

## Step B — Ubuntu SSH trigger installation

1. Check that the original deploy agent is healthy: `python3 scripts/freesize_local_deploy.py status` shows `ENABLED`, and the timer is `active`. Verify `/api/ready`. Existing web should still work.
2. Install OpenSSH server **only if not already installed**. If installation starts the standard Ubuntu SSH service on port 22, review its existing firewall and networking exposure; **the separate trigger service itself uses port 2222 bound to Tailscale only**.
3. Generate a dedicated SSH key (the PRIVATE key will go to a GitHub Actions secret, **never chat or Git**):

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
umask 077
ssh-keygen -t ed25519 -f ~/.ssh/freesize-gha-trigger -C 'freesize-gha-trigger' -N ''
```

4. From repo root run:

```bash
bash scripts/install_freesize_push_trigger.sh ~/.ssh/freesize-gha-trigger.pub
```

This will require `sudo` and create a `freesize-trigger` Linux user, `/etc/ssh/freesize-push-sshd.conf`, `freesize-push-trigger.service`, a limited `/etc/sudoers.d/freesize-ci-trigger`, and two root-owned executable scripts. It does not restart Docker or database containers.

5. The installer prints `FREESIZE_TAILSCALE_IP` and a **public** host-key line for GitHub secret `FREESIZE_SSH_KNOWN_HOSTS`. Save those outputs. Compare the printed SSH fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Do not use an unverified key fetched from the GitHub runner.

6. Inspect listener: `sudo ss -lntp '( sport = :2222 )'`. **Listen address must equal the exact Tailscale `100.x.x.x`, never `0.0.0.0` or `::`.**

## Step C — Tailscale network identity and policy

1. Go to <https://login.tailscale.com/admin/acls> and define `tag:freesize-ci` in existing `tagOwners` (e.g. `"tag:freesize-ci": ["autogroup:admin"]`). Do not replace the rest of the tailnet policy.
2. In Access Controls add a grant to let `tag:freesize-ci` reach ONLY the Ubuntu Tailscale IPv4 address on TCP 2222 (substitute printed IP):

```json
{
  "src": ["tag:freesize-ci"],
  "dst": ["100.x.y.z/32"],
  "ip": ["tcp:2222"]
}
```

**Important:** if the policy also contains `"src":["*"],"dst":["*"],"ip":["*"]`, the broad rule still allows the CI tag more than TCP 2222; review and narrow broad rules if safe. Preserve existing friends' Tailscale access. This pack does **not** edit access controls automatically.

3. Go to <https://login.tailscale.com/admin/settings/oauth> (or Admin Console → Trust credentials / OAuth Clients; location may vary). Create an OAuth client permitted to create ephemeral devices using writable `auth_keys` and `tag:freesize-ci`. Save client ID and secret privately. Tailscale's official GitHub action supports these inputs.

## Step D — GitHub Actions secrets and enable flag

Repo: <https://github.com/ratamon2606-create/freesize/settings/secrets/actions>

Set **repository secrets**:

- `TS_OAUTH_CLIENT_ID` = Tailscale OAuth client ID
- `TS_OAUTH_SECRET` = Tailscale OAuth secret
- `FREESIZE_SSH_PRIVATE_KEY` = full **private key file**, including BEGIN/END lines, `~/.ssh/freesize-gha-trigger`. Paste via GitHub UI **only**, or authenticated `gh secret set FREESIZE_SSH_PRIVATE_KEY < ~/.ssh/freesize-gha-trigger`; NEVER print/paste it in chat.
- `FREESIZE_SSH_KNOWN_HOSTS` = single host-key line the installer printed, e.g. `[100.x.y.z]:2222 ssh-ed25519 AAAA...`. This is a PUBLIC host key, not a private key.

Set **repository variable**:

- `FREESIZE_TAILSCALE_IP` = the IPv4 printed by installer, e.g. `100.x.y.z`
- `FREESIZE_PUSH_DEPLOY_ENABLED` = **`true`** **only after** host SSH service and Tailscale policy are verified and at least 1 review is enforced in GitHub Ruleset.

## Step E — One end-to-end deployment test

1. Create an ordinary small UI PR (e.g. visible text), get review 1/1 + CI 5/5 and merge to `main`.
2. Go to GitHub Actions → `Publish and deploy verified release`, look for job `Trigger private laptop deploy after GHCR publish` **after** `Publish immutable GHCR images` succeeds.
3. Job must print `DEPLOY VERIFIED: <new-sha>`. Then on laptop verify:

```bash
python3 scripts/freesize_local_deploy.py status
journalctl -u freesize-local-deploy.service -n 70 --no-pager
curl -fsS https://tcrose-loq-15iax9e.taila69f2a.ts.net/api/ready | python3 -m json.tool
systemctl is-active freesize-local-deploy.timer
```

4. The 30-minute timer remains `active`; it backs up the event trigger. Browser refresh (Ctrl+Shift+R) if UI still looks cached.

## Diagnostics / emergency stop

- Tailscale runner can't connect: verify OAuth tag, grant to correct IP TCP 2222, service `sudo systemctl status freesize-push-trigger.service`, `sudo ss -lntp '( sport = :2222 )'`, and local tailscale health. If Tailscale IPv4 changes, re-run installer and update GitHub variable plus known-hosts entry.
- Wrong SSH host key: stop. Check the real SSH host key fingerprint on Ubuntu; do not disable `StrictHostKeyChecking`.
- Deploy agent PAUSED: investigate `journalctl -u freesize-local-deploy.service`; do NOT automatically resume on GitHub.
- Trigger job fails but image published: timer still checks releases and can deploy while `ENABLED` and healthy.
- Stop event notifications immediately: Set GitHub repo variable `FREESIZE_PUSH_DEPLOY_ENABLED=false`. Existing timer remains unaffected.
- Disable private SSH listener: `sudo systemctl disable --now freesize-push-trigger.service`.
- Revoke access: remove `tag:freesize-ci` grant and delete GitHub SSH/OAuth secrets; rotate the dedicated SSH key if compromise is suspected.

## Security caveats

This is a self-hosted laptop in a private Tailnet, not a public VPS. If the laptop is sleeping/offline or GHCR unavailable, no instant deploy occurs. The existing timer is the fallback on recovery; user data requires separately verified backups/restores. Database schema migrations are not rolled back by web-image rollback. Running GitHub Actions after a human-reviewed main branch still executes repository-controlled scripts; protect `.github/workflows/**` with review and preferably CODEOWNERS before giving broad collaborator write permissions.

Sources: https://github.com/tailscale/github-action/blob/main/README.md ; https://tailscale.com/docs/features/oauth-clients ; https://tailscale.com/docs/reference/syntax/grants ; https://docs.github.com/en/actions/reference/security/secure-use
