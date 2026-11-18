#!/usr/bin/env python3
"""Safe, opt-in deployment of CI-published GHCR image to local freesize-friends.

Requires a healthy existing stack, secure Tailscale Serve, and substantial free
Docker-host disk. Never changes database services or volumes. Does not git pull.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = "ratamon2606-create/freesize"
API = f"https://api.github.com/repos/{REPO}"
IMAGE = "ghcr.io/ratamon2606-create/freesize-web"
ROOT = Path(__file__).resolve().parents[1]
APP_HOME = Path.home() / ".local/state/freesize-local-deploy"
PAUSE_FILE = APP_HOME / "PAUSED"
ENABLE_FILE = APP_HOME / "ENABLED"
STATE_FILE = APP_HOME / "state.json"
ENV = ROOT / ".env.friends"
BASE = ROOT / "docker-compose.yml"
FRIENDS = ROOT / "compose.friends.yml"
OVERRIDE = ROOT / "compose.autodeploy.yml"
CONTAINER = "freesize-friends-web-1"
BACKUP_TAG = "freesize-local-rollback:previous"
MIN_FREE_GIB = 12
MIN_ROOT_FREE_GIB_WHEN_DOCKER_SEPARATE = 6


class Stop(Exception):
    pass


def log(message: str) -> None:
    print(f"[freesize-local-deploy] {message}", flush=True)


def run(*args: str, env: dict[str, str] | None = None, capture: bool = False) -> str:
    command = list(args)
    try:
        p = subprocess.run(command, cwd=ROOT, env=env, text=True, timeout=240,
                           stdout=subprocess.PIPE if capture else None,
                           stderr=subprocess.PIPE if capture else None, check=False)
    except subprocess.TimeoutExpired as exc:
        raise Stop(f"Command timed out after 240s: {' '.join(command)}") from exc
    if p.returncode != 0:
        err = (p.stderr or "").strip()[-700:]
        raise Stop(f"Command failed ({p.returncode}): {' '.join(command)}; {err}")
    return (p.stdout or "").strip()


def compose(*arguments: str, image: str | None = None) -> str:
    e = os.environ.copy()
    if image:
        e["FREESIZE_RELEASE_IMAGE"] = image
    return run("docker", "compose", "--env-file", str(ENV), "-p", "freesize-friends",
               "-f", str(BASE), "-f", str(FRIENDS), "-f", str(OVERRIDE),
               *arguments, env=e, capture=False)


def api_json(path: str) -> dict[str, Any]:
    request = urllib.request.Request(API + path, headers={"User-Agent": "FreesizeLocalReleaseAgent/1.0",
                                                      "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise Stop(f"GitHub API unavailable or limited: {exc}") from exc


def env_values() -> dict[str, str]:
    if not ENV.is_file() or not BASE.is_file() or not FRIENDS.is_file() or not OVERRIDE.is_file():
        raise Stop("Missing .env.friends / Compose files. Run install script first.")
    vals: dict[str, str] = {}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        vals[key.strip()] = value.strip().strip('"').strip("'")
    if vals.get("WEB_PORT") != "3100":
        raise Stop("Expected dedicated WEB_PORT=3100; do not touch other deployments.")
    if vals.get("SEED_DEMO") != "0":
        raise Stop("SEED_DEMO must be 0 before shared updates.")
    if vals.get("COOKIE_SECURE") != "1":
        raise Stop("COOKIE_SECURE must be 1 for HTTPS shared site.")
    if not re.fullmatch(r"[0-9a-fA-F]{48,}", vals.get("REGISTRATION_INVITE_CODE", "")):
        raise Stop("Secure REGISTRATION_INVITE_CODE must be >=48 hex characters.")
    origin = vals.get("PUBLIC_ORIGIN", "")
    if not re.fullmatch(r"https://[a-z0-9-]+\.[a-z0-9-]+\.ts\.net", origin):
        raise Stop("PUBLIC_ORIGIN must be your exact Tailscale HTTPS URL (no trailing slash).")
    over = OVERRIDE.read_text()
    if "FREESIZE_RELEASE_IMAGE" not in over:
        raise Stop("Compose auto-deploy override file is invalid.")
    if "PUBLIC_ORIGIN" not in FRIENDS.read_text():
        raise Stop("Missing PUBLIC_ORIGIN in compose.friends.yml override.")
    return vals


def disk_check() -> None:
    """Require headroom on both filesystems, not 12 GiB on an unrelated /.

    If Docker uses the root filesystem, keep the original 12 GiB floor.
    If Docker lives on a separate mounted filesystem (e.g. /mnt/rosdata),
    require 12 GiB on Docker's filesystem and 6 GiB on / for OS and logs.
    Fail closed if Docker storage location cannot be verified.
    """
    raw = run("docker", "info", "--format", "{{.DockerRootDir}}", capture=True).strip()
    docker_root = Path(raw)
    if not raw or not docker_root.is_absolute():
        raise Stop("Docker data-root path is missing or not absolute; refusing deploy.")
    try:
        root_stat = os.stat("/")
        docker_stat = os.stat(docker_root)
        root_free = shutil.disk_usage("/").free / (1024**3)
        docker_free = shutil.disk_usage(docker_root).free / (1024**3)
    except OSError as exc:
        raise Stop(f"Cannot verify root and Docker storage: {type(exc).__name__}; refusing deploy.") from exc
    separate = root_stat.st_dev != docker_stat.st_dev
    root_required = MIN_ROOT_FREE_GIB_WHEN_DOCKER_SEPARATE if separate else MIN_FREE_GIB
    if root_free < root_required:
        raise Stop(f"Insufficient root disk: {root_free:.1f} GiB free, require >= {root_required} GiB; no images or volumes removed.")
    if separate and docker_free < MIN_FREE_GIB:
        raise Stop(f"Insufficient Docker data-root disk: {docker_free:.1f} GiB free, require >= {MIN_FREE_GIB} GiB; no images or volumes removed.")
    log(f"Disk check OK: root {root_free:.1f} GiB; Docker {docker_free:.1f} GiB "
        f"({'separate' if separate else 'same'} filesystem).")


def get_json(url: str, timeout: int = 6) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise Stop(f"Health endpoint failed at {url}: {type(exc).__name__}") from exc


def assert_health(vals: dict[str, str], attempts: int = 1) -> None:
    origin = vals["PUBLIC_ORIGIN"]
    last = "No health checks performed"
    for i in range(attempts):
        try:
            for url in ("http://127.0.0.1:3100", origin):
                d = get_json(url + "/api/ready")
                if not (d.get("ready") is True and d.get("sql") == "PostgreSQL"
                        and d.get("nosql") == "MongoDB"):
                    raise Stop(f"Readiness rejected for {url}: {d.get('problems')}")
                if vals.get("REQUIRE_BACKGROUND_AI") == "1" and d.get("background_ai") != "in-process model ready":
                    raise Stop(f"Local AI not ready at {url}.")
            health = get_json("http://127.0.0.1:3100/api/health")
            if health.get("demo_enabled") is not False or health.get("invite_required") is not True:
                raise Stop("Demo or invite registration check failed.")
            return
        except Stop as exc:
            last = str(exc)
            if i < attempts - 1:
                time.sleep(3)
    raise Stop(last)


def choose_published_release(main_sha: str, runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Accept only a successful release workflow for this *current* main SHA."""
    for item in runs:
        if (item.get("name") == "Publish and deploy verified release"
                and item.get("event") == "workflow_run"
                and item.get("head_branch") == "main"
                and item.get("head_sha") == main_sha
                and item.get("status") == "completed"
                and item.get("conclusion") == "success"):
            return item
    return None


def verify_merge_policy() -> None:
    """Require an active PR rule with >=1 approval and five current CI checks.

    Resolve repository rulesets dynamically: GitHub assigns a new ID every time
    a ruleset is recreated. Signed commits are optional by team choice.
    """
    expected = {
        "Unit and API (preview)",
        "Browser UI smoke",
        "Hybrid DB (PostgreSQL + MongoDB)",
        "Build container images",
        "Dependency vulnerability audit",
    }
    summaries = api_json("/rulesets?per_page=100")
    if not isinstance(summaries, list):
        raise Stop("GitHub ruleset list was invalid; refusing deployment.")
    for info in summaries:
        if not isinstance(info, dict):
            continue
        if info.get("target") != "branch" or info.get("enforcement") != "active":
            continue
        rule_id = info.get("id")
        if not isinstance(rule_id, int) or rule_id < 1:
            continue
        policy = api_json(f"/rulesets/{rule_id}")
        if policy.get("enforcement") != "active" or policy.get("target") != "branch":
            continue
        refs = policy.get("conditions", {}).get("ref_name", {})
        included = refs.get("include", [])
        excluded = refs.get("exclude", [])
        matches_main = any(x in included for x in ("~DEFAULT_BRANCH", "refs/heads/main", "main"))
        excludes_main = any(x in excluded for x in ("~DEFAULT_BRANCH", "refs/heads/main", "main"))
        if not matches_main or excludes_main:
            continue
        rules = {r.get("type"): r for r in policy.get("rules", []) if isinstance(r, dict)}
        if not {"pull_request", "required_status_checks"}.issubset(rules):
            continue
        approvals = rules["pull_request"].get("parameters", {}).get("required_approving_review_count", 0)
        if not isinstance(approvals, int) or approvals < 1:
            continue
        checks = rules["required_status_checks"].get("parameters", {})
        names = {c.get("context") for c in checks.get("required_status_checks", []) if isinstance(c, dict)}
        if not expected.issubset(names):
            continue
        if checks.get("strict_required_status_checks_policy") is not True:
            continue
        log(f"GitHub PR protection verified: ruleset {rule_id}, approvals {approvals}, five CI checks; signatures optional.")
        return
    raise Stop("No active main branch ruleset requiring 1 review, current-branch checks, and all five CI jobs; refusing deployment.")


def published_sha() -> str | None:
    branch = api_json("/branches/main")
    sha = branch.get("commit", {}).get("sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise Stop("Bad main SHA from GitHub.")
    run_items = api_json("/actions/runs?per_page=30").get("workflow_runs", [])
    item = choose_published_release(sha, run_items)
    if not item:
        log(f"No completed successful GHCR publish for current main {sha[:12]} yet. No deployment.")
        return None
    jobs = api_json(f"/actions/runs/{item['id']}/jobs?per_page=30").get("jobs", [])
    if not any(x.get("name") == "Publish immutable GHCR images" and x.get("conclusion") == "success" for x in jobs):
        log("Release workflow did not contain a successful GHCR publish job. No deployment.")
        return None
    # Avoid releasing an old SHA if main moves during the check.
    latest = api_json("/branches/main").get("commit", {}).get("sha")
    if latest != sha:
        log("main changed during preflight; checking again on the next timer tick.")
        return None
    return sha


def get_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(values: dict[str, Any]) -> None:
    APP_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    APP_HOME.chmod(0o700)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(STATE_FILE)


def ensure_paused() -> None:
    APP_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    APP_HOME.chmod(0o700)
    PAUSE_FILE.touch(mode=0o600, exist_ok=True)
    ENABLE_FILE.unlink(missing_ok=True)


def image_digest_for(tag: str) -> str:
    obj = json.loads(run("docker", "image", "inspect", tag, "--format", "{{json .RepoDigests}}", capture=True))
    matches = [s for s in obj if s.startswith(IMAGE + "@sha256:")]
    if not matches or not re.fullmatch(re.escape(IMAGE) + r"@sha256:[a-f0-9]{64}", matches[0]):
        raise Stop("Pulled image has no expected GHCR immutable digest; refusing deploy.")
    return matches[0]


def running_image_id() -> str:
    ident = run("docker", "inspect", CONTAINER, "--format", "{{.Image}}", capture=True)
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", ident):
        raise Stop(f"Unexpected running web container image ID: {ident}")
    return ident


def swap_web(image: str) -> None:
    # --no-deps never starts/stops DB containers; --no-build avoids root disk spikes.
    compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "web", image=image)


def deployed_digest() -> str | None:
    s = get_state()
    return s.get("image")


def check_once() -> None:
    if PAUSE_FILE.exists() or not ENABLE_FILE.exists():
        log("PAUSED (default). No deploy. See status/resume.")
        return
    vals = env_values()
    disk_check()
    assert_health(vals, attempts=1)  # Refuse to deploy into an unhealthy service.
    verify_merge_policy()
    sha = published_sha()
    if sha is None:
        return
    old = get_state()
    if old.get("sha") == sha:
        try:
            claimed_id = run("docker", "image", "inspect", old["image"], "--format", "{{.Id}}", capture=True)
            if claimed_id == running_image_id():
                log(f"Already deployed {sha[:12]}.")
                return
        except (Stop, KeyError):
            pass
        raise Stop("Live web image does not match recorded release; pause and investigate manual changes.")
    # When earlier deploy failed, pause rather than repeatedly breaking the website.
    if old.get("blocked_sha") == sha:
        log("Candidate was previously rejected; awaiting operator review.")
        return
    tag = IMAGE + ":sha-" + sha
    log(f"Found verified CI release {sha[:12]}; pulling GHCR image (not building locally).")
    run("docker", "pull", tag)
    target = image_digest_for(tag)
    # Confirm no repo branch movement *after* slow Docker pull.
    if api_json("/branches/main").get("commit", {}).get("sha") != sha:
        log("main changed during Docker pull; leaving running web unchanged.")
        return
    disk_check()
    assert_health(vals, attempts=1)
    previous_id = running_image_id()
    if previous_id == run("docker", "image", "inspect", target, "--format", "{{.Id}}", capture=True):
        save_state({"sha": sha, "image": target, "previous_sha": old.get("sha")})
        log("Container already runs the released image; saved deployment state.")
        return
    run("docker", "tag", previous_id, BACKUP_TAG)
    log("Pinned previous running web image locally for rollback.")
    try:
        swap_web(target)
        assert_health(vals, attempts=30)
    except Exception as exc:
        log(f"Deployment failed: {exc}. Attempting rollback of web only.")
        ensure_paused()
        new_state = {**old, "blocked_sha": sha, "last_failure": str(exc)}
        save_state(new_state)
        try:
            swap_web(BACKUP_TAG)
            assert_health(vals, attempts=30)
            log("AUTO-ROLLBACK VERIFIED: previous web restored. Agent remains PAUSED.")
        except Exception as rb_exc:
            log(f"CRITICAL: rollback did not recover health: {rb_exc}")
            raise Stop("Deployment and rollback failed; manual repair required.") from rb_exc
        return
    save_state({"sha": sha, "image": target, "previous_sha": old.get("sha")})
    log(f"DEPLOY SUCCESS: {sha[:12]} with healthy PostgreSQL/MongoDB and HTTPS. Previous web image tagged.")


def rollback() -> None:
    vals = env_values()
    try:
        run("docker", "image", "inspect", BACKUP_TAG, "--format", "{{.Id}}", capture=True)
    except Stop as exc:
        raise Stop("No verified previous web image available; cannot rollback.") from exc
    state = get_state()
    ensure_paused()  # Do not immediately redeploy the same bad current SHA.
    live_id = running_image_id()
    rescue_tag = "freesize-local-rollback:before-manual"
    run("docker", "tag", live_id, rescue_tag)
    log("Manual rollback requested: paused agent and restoring previous web image.")
    try:
        swap_web(BACKUP_TAG)
        assert_health(vals, attempts=30)
    except Exception as exc:
        log(f"Manual rollback target unhealthy: {exc}. Trying to recover original image.")
        try:
            swap_web(rescue_tag)
            assert_health(vals, attempts=30)
            log("Original web recovered. Agent remains PAUSED for investigation.")
        except Exception as recover_exc:
            raise Stop(f"CRITICAL: original and rollback unhealthy: {recover_exc}") from recover_exc
        raise Stop("Rollback target failed health; original restored and agent PAUSED.") from exc
    save_state({"sha": state.get("previous_sha"), "image": BACKUP_TAG,
                "blocked_sha": state.get("sha"), "last_failure": "operator manual rollback"})
    log("MANUAL ROLLBACK VERIFIED; agent remains PAUSED.")


def dispatch(action: str) -> None:
    """Execute one requested action with the process-wide lock already held."""
    APP_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    APP_HOME.chmod(0o700)
    if action == "pause":
        ensure_paused()
        log("PAUSED: timer may run but will not deploy.")
    elif action == "status":
        s = get_state()
        log("PAUSED" if PAUSE_FILE.exists() or not ENABLE_FILE.exists() else "ENABLED")
        log(f"Known deployed commit: {(s.get('sha') or 'unknown')[:12]}")
        log(f"Last failed candidate: {(s.get('blocked_sha') or 'none')[:12]}")
    elif action == "resume":
        vals = env_values()
        disk_check()
        assert_health(vals, attempts=1)
        verify_merge_policy()
        PAUSE_FILE.unlink(missing_ok=True)
        ENABLE_FILE.touch(mode=0o600, exist_ok=True)
        log("ENABLED: preflight passed. Next timer tick can deploy a new successful release.")
    elif action == "plan":
        env_values()
        sha = published_sha()
        log(f"Latest published main: {(sha or 'not available')[:12]}")
        log("PLAN ONLY: did not pull images or change containers.")
    elif action == "check":
        check_once()
    elif action == "rollback":
        rollback()


def main() -> None:
    parser = argparse.ArgumentParser(description="Freesize local CI-gated auto-deployment")
    parser.add_argument("action", choices=["check", "status", "pause", "resume", "rollback", "plan"])
    args = parser.parse_args()
    APP_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    APP_HOME.chmod(0o700)
    # Prevent concurrent timer, command-line and manual rollback from racing.
    lockfile = APP_HOME / "agent.lock"
    with lockfile.open("a+") as lock:
        lockfile.chmod(0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        dispatch(args.action)


if __name__ == "__main__":
    try:
        main()
    except Stop as error:
        log(f"STOP: {error}")
        sys.exit(2)
