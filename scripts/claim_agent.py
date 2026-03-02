"""
claim_agent.py — Platinum Tier
================================
Watches vault/Needs_Action/<domain>/ for new task files (.json / .md)
and atomically claims them by moving to vault/In_Progress/<agent>/.

Claim-by-move rule
------------------
  RENAME is atomic on the same filesystem (Linux: os.rename / Windows: os.replace).
  Only one agent wins the race — the loser gets FileNotFoundError and skips.

Dead-agent recovery
-------------------
  Any task sitting in In_Progress/<agent>/ longer than CLAIM_TIMEOUT_SEC
  is moved back to Needs_Action/<domain>/ so another agent can pick it up.

Usage
-----
    python scripts/claim_agent.py --agent orchestrator [--domain email] [--poll 3]

    --agent   : agent identity (must match a folder in vault/In_Progress/)
    --domain  : optional — only claim from this domain subfolder
                omit to claim from ALL domain folders
    --poll    : polling interval in seconds (default 3)

Install dependency (polling fallback — no native FS events needed):
    pip install watchdog          # optional but faster on Linux inotify
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path bootstrap — import siblings
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from audit_logger import (
    AuditLogger,
    EV_TASK_CREATED, EV_TASK_HANDLED, EV_ALERT, EV_START, EV_STOP,
    SEV_WARN,
)

VAULT          = PROJECT_ROOT / "vault"
NEEDS_ACTION   = VAULT / "Needs_Action"
IN_PROGRESS    = VAULT / "In_Progress"

PKT            = timezone(timedelta(hours=5))
CLAIM_TIMEOUT_SEC = 3600          # 1 hour — then return to queue
TASK_EXTENSIONS   = {".json", ".md"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now(tz=PKT).isoformat()


def pkt_now() -> datetime:
    return datetime.now(tz=PKT)


def candidate_folders(domain: Optional[str]) -> list[Path]:
    """Return list of Needs_Action subfolders this agent should watch."""
    if domain:
        return [NEEDS_ACTION / domain]
    return [p for p in NEEDS_ACTION.iterdir() if p.is_dir() and not p.name.startswith(".")]


def is_task_file(path: Path) -> bool:
    return path.suffix.lower() in TASK_EXTENSIONS and not path.name.startswith(".")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

class ClaimAgent:
    def __init__(self, agent: str, domain: Optional[str], poll: int) -> None:
        self.agent  = agent
        self.domain = domain
        self.poll   = poll
        self.log    = AuditLogger(f"claim_agent.{agent}")

        # Ensure agent's In_Progress folder exists
        self.inbox = IN_PROGRESS / agent
        self.inbox.mkdir(parents=True, exist_ok=True)

        self.log.info(EV_START, agent=agent, domain=domain or "all", poll_sec=poll)
        print(f"[ClaimAgent:{agent}] started | domain={domain or 'all'} | poll={poll}s")

    # ------------------------------------------------------------------
    # Claim a single file — atomic rename wins the race
    # ------------------------------------------------------------------

    def try_claim(self, src: Path) -> bool:
        """
        Attempt to claim src by moving it to inbox.
        Returns True if claimed, False if lost the race.
        """
        dest = self.inbox / src.name

        # If a file with same name already exists in inbox, skip (duplicate)
        if dest.exists():
            return False

        try:
            # os.replace is atomic on POSIX; raises on cross-device move
            # shutil.move handles cross-device but isn't atomic — use rename first
            src.rename(dest)
            self.log.info(
                EV_TASK_CREATED,
                action="claimed",
                agent=self.agent,
                task=src.name,
                domain=src.parent.name,
                dest=str(dest.resolve().relative_to(PROJECT_ROOT)),
            )
            print(f"  [CLAIMED] {src.name}  ->  In_Progress/{self.agent}/")
            return True

        except FileNotFoundError:
            # Another agent got it first — normal race condition
            return False
        except OSError as e:
            self.log.error(EV_ALERT, action="claim_failed", task=src.name, error=str(e))
            return False

    # ------------------------------------------------------------------
    # Scan Needs_Action folders
    # ------------------------------------------------------------------

    def scan_and_claim(self) -> int:
        claimed = 0
        for folder in candidate_folders(self.domain):
            if not folder.exists():
                continue
            for f in sorted(folder.iterdir()):   # oldest first (sorted by name)
                if is_task_file(f):
                    if self.try_claim(f):
                        claimed += 1
        return claimed

    # ------------------------------------------------------------------
    # Dead-agent recovery — return stale tasks to Needs_Action
    # ------------------------------------------------------------------

    def recover_stale(self) -> int:
        """Move tasks that have been In_Progress too long back to queue."""
        recovered = 0
        cutoff = pkt_now().timestamp() - CLAIM_TIMEOUT_SEC

        for agent_dir in IN_PROGRESS.iterdir():
            if not agent_dir.is_dir():
                continue
            for f in agent_dir.iterdir():
                if not is_task_file(f):
                    continue
                try:
                    age = pkt_now().timestamp() - f.stat().st_mtime
                    if age > CLAIM_TIMEOUT_SEC:
                        # Determine which domain folder to return to
                        domain = _infer_domain(f)
                        dest_dir = NEEDS_ACTION / domain
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        dest = dest_dir / f.name

                        f.rename(dest)
                        self.log.warn(
                            EV_ALERT,
                            action="stale_task_recovered",
                            task=f.name,
                            from_agent=agent_dir.name,
                            age_sec=int(age),
                            returned_to=domain,
                        )
                        print(f"  [RECOVER] {f.name} stale in {agent_dir.name}/ → returned to {domain}/")
                        recovered += 1
                except (FileNotFoundError, OSError):
                    pass

        return recovered

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        recovery_interval = 60      # run recovery every 60s
        last_recovery = 0.0

        try:
            while True:
                # Claim new tasks
                self.scan_and_claim()

                # Periodic stale-task recovery
                now = time.monotonic()
                if now - last_recovery > recovery_interval:
                    self.recover_stale()
                    last_recovery = now

                time.sleep(self.poll)

        except KeyboardInterrupt:
            self.log.info(EV_STOP, agent=self.agent)
            print(f"\n[ClaimAgent:{self.agent}] stopped.")


# ---------------------------------------------------------------------------
# Domain inference from filename
# ---------------------------------------------------------------------------

DOMAIN_KEYWORDS = {
    "email":    ["email", "gmail", "mail", "inbox"],
    "odoo":     ["odoo", "crm", "invoice", "lead", "sale", "purchase"],
    "social":   ["twitter", "linkedin", "meta", "instagram", "post"],
    "calendar": ["calendar", "meeting", "event", "schedule"],
}


def _infer_domain(f: Path) -> str:
    """Guess domain from filename for stale-recovery routing."""
    name_lower = f.stem.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return domain
    return "general"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Claim-by-move agent for vault/Needs_Action/")
    parser.add_argument("--agent",  required=True, help="Agent identity (e.g. orchestrator)")
    parser.add_argument("--domain", default=None,  help="Limit to one domain folder (optional)")
    parser.add_argument("--poll",   type=int, default=3, help="Poll interval in seconds (default 3)")
    args = parser.parse_args()

    # Validate agent folder exists (setup_vault_structure.py must run first)
    agent_dir = IN_PROGRESS / args.agent
    if not agent_dir.exists():
        print(f"ERROR: {agent_dir} does not exist. Run: python scripts/setup_vault_structure.py")
        sys.exit(1)

    ClaimAgent(agent=args.agent, domain=args.domain, poll=args.poll).run()


if __name__ == "__main__":
    main()
