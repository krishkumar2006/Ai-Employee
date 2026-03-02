"""
vault_sync_windows.py — Platinum Tier
=======================================
Python equivalent of vault_sync.sh for Windows (no bash required).
Runs via Windows Task Scheduler every 5 minutes.

Called by: setup_sync_windows.ps1
Logs to:   vault/Logs/sync_windows.log
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT        = PROJECT_ROOT / "vault"
LOCK_FILE    = Path(os.environ.get("TEMP", "/tmp")) / "vault_sync.lock"
LOG_FILE     = VAULT / "Logs" / "sync_windows.log"
BRANCH       = os.environ.get("VAULT_SYNC_BRANCH", "main")
REMOTE       = os.environ.get("VAULT_SYNC_REMOTE", "origin")

PKT = timezone(timedelta(hours=5))

# Vault paths to stage (secrets excluded by .gitignore, but double-checked here)
SYNC_PATHS = [
    "vault/Needs_Action",
    "vault/Plans",
    "vault/Pending_Approval",
    "vault/Done",
    "vault/Updates",
    "vault/Dashboard.md",
    "vault/SKILLS.md",
    "vault/Business_Goals.md",
    "vault/Company_Handbook.md",
]

# These paths must NEVER be staged regardless of .gitignore
NEVER_STAGE = [".env", "*.key", "*.pem", "credentials.json", "token.json"]


def ts() -> str:
    return datetime.now(tz=PKT).strftime("%Y-%m-%d %H:%M:%S PKT")


def log(msg: str) -> None:
    line = f"[vault_sync {ts()}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the repo root."""
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd, cwd=str(PROJECT_ROOT),
        capture_output=True, text=True,
        check=check
    )


def main() -> None:
    os.chdir(PROJECT_ROOT)

    # --- Lock check ---
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < 300:
            log(f"Another sync running (lock {int(age)}s old). Skipping.")
            return
        log(f"Stale lock ({int(age)}s). Removing.")
    LOCK_FILE.touch()

    try:
        _sync()
    finally:
        LOCK_FILE.unlink(missing_ok=True)


def _sync() -> None:
    log(f"Starting sync | branch={BRANCH} remote={REMOTE}")

    # 1. Pull with rebase
    log("Pulling...")
    result = run_git("pull", "--rebase", REMOTE, BRANCH, check=False)
    if result.returncode != 0:
        log(f"Pull failed: {result.stderr.strip()}")
        run_git("rebase", "--abort", check=False)
        log("Pull skipped — will retry next cycle.")
    else:
        log("Pull OK.")

    # 2. Stage sync paths
    log("Staging vault paths...")
    staged_any = False
    for path_str in SYNC_PATHS:
        full = PROJECT_ROOT / path_str
        if full.exists():
            run_git("add", path_str, check=False)
            staged_any = True

    # 3. Commit if anything staged
    result = run_git("diff", "--cached", "--quiet", check=False)
    if result.returncode == 0:
        log("Nothing to commit. Vault is up to date.")
    else:
        commit_msg = f"vault-sync: auto {datetime.now(tz=PKT).strftime('%Y-%m-%d %H:%M PKT')}"
        run_git("commit", "-m", commit_msg,
                "--author=VaultSync Bot <vault-sync@ai-employee.local>")
        log(f"Committed: {commit_msg}")

        # 4. Push
        log("Pushing...")
        result = run_git("push", REMOTE, BRANCH, check=False)
        if result.returncode != 0:
            log(f"Push failed: {result.stderr.strip()} — retrying in 5s...")
            time.sleep(5)
            result = run_git("push", REMOTE, BRANCH, check=False)
            if result.returncode != 0:
                log(f"Push failed again. Will retry next cycle.")
                err = {"ts": ts(), "error": "push_failed", "detail": result.stderr.strip()}
                import json
                (VAULT / "Logs" / "sync_error.json").write_text(
                    json.dumps(err, indent=2), encoding="utf-8"
                )
                return
        log("Push OK.")
        (VAULT / "Logs" / "sync_error.json").unlink(missing_ok=True)

    log("Sync complete.")


if __name__ == "__main__":
    main()
