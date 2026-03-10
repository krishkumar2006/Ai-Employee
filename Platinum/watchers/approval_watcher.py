"""
approval_watcher.py — Platinum Tier (LOCAL only)
=================================================
Watches vault/Pending_Approval/<domain>/ for draft files placed by
the cloud agent (or any upstream producer).

Claim-by-move prevents double-work:
  1. Check In_Progress/<any>/ — if already claimed there, skip.
  2. Atomic rename → In_Progress/approval_watcher/<file>
     Only one process wins; others get FileNotFoundError → skip.

Auto-approve logic (set AUTO_APPROVE_BELOW in .env.local):
  "none"   → never auto-approve (safe default)
  "low"    → auto-approve drafts whose priority == "low"
  "medium" → auto-approve if priority in {low, medium}
  "all"    → approve everything automatically

After decision:
  Approved  → vault/Approved/<domain>/
  Rejected  → vault/Rejected/<domain>/
  Human TBD → stays in In_Progress/approval_watcher/ with a .pending_ marker

Human commands (CLI):
  python watchers/approval_watcher.py --approve FILENAME
  python watchers/approval_watcher.py --reject  FILENAME

Daemon (default):
  python watchers/approval_watcher.py [--poll 15] [--verbose]
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from audit_logger import AuditLogger, EV_TASK_CREATED, EV_ODOO_ACTION, EV_ALERT

# ---------------------------------------------------------------------------
# Dry-run helper (shared config if available, env fallback otherwise)
# ---------------------------------------------------------------------------
try:
    from config import cfg as _cfg
    def _is_dry_run() -> bool:
        return _cfg.is_dry_run()
except ImportError:
    import os as _os
    def _is_dry_run() -> bool:  # type: ignore[misc]
        return _os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

VAULT            = PROJECT_ROOT / "vault"
PENDING_APPROVAL = VAULT / "Pending_Approval"
IN_PROGRESS      = VAULT / "In_Progress"
APPROVED         = VAULT / "Approved"
REJECTED         = VAULT / "Rejected"

PKT          = timezone(timedelta(hours=5))
AGENT_NAME   = "approval_watcher"
IN_PROG_DIR  = IN_PROGRESS / AGENT_NAME
POLL_INTERVAL = 15   # seconds

# Priority ordering for auto-approve threshold comparisons
PRIORITY_RANK: dict[str, int] = {
    "low":      1,
    "medium":   2,
    "high":     3,
    "critical": 4,
}

# Domains we know about (used for inference fallback)
KNOWN_DOMAINS = {"email", "odoo", "social", "calendar", "general"}

log = AuditLogger(AGENT_NAME)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        from config import cfg
        return {
            "mode":         cfg.get("DEPLOYMENT_MODE", "local").lower(),
            "auto_approve": cfg.get("AUTO_APPROVE_BELOW", "none").lower(),
        }
    except ImportError:
        import os
        return {
            "mode":         os.environ.get("DEPLOYMENT_MODE", "local").lower(),
            "auto_approve": os.environ.get("AUTO_APPROVE_BELOW", "none").lower(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_already_claimed(filename: str) -> bool:
    """Return True if this filename already exists in any In_Progress/<agent>/ dir."""
    if not IN_PROGRESS.exists():
        return False
    for agent_dir in IN_PROGRESS.iterdir():
        if agent_dir.is_dir() and (agent_dir / filename).exists():
            return True
    return False


def _try_claim(src: Path) -> Optional[Path]:
    """
    Atomically claim src by renaming it to In_Progress/approval_watcher/<name>.

    Returns the new path on success, None if the file was already taken
    (FileNotFoundError from a concurrent rename).
    """
    IN_PROG_DIR.mkdir(parents=True, exist_ok=True)
    dest = IN_PROG_DIR / src.name
    try:
        src.rename(dest)
        log.info(EV_TASK_CREATED, action="claimed", file=src.name,
                 src=str(src.relative_to(VAULT)))
        return dest
    except FileNotFoundError:
        return None  # another process got it first


def _read_draft(path: Path) -> Optional[dict]:
    """
    Parse a draft file. Supports JSON or Markdown with YAML frontmatter.
    Returns a dict with at minimum {"priority": str, "body": str}.
    """
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            return json.loads(text)
        # Try YAML frontmatter
        m = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
        if m:
            try:
                import yaml
                front = yaml.safe_load(m.group(1)) or {}
                front["body"] = m.group(2).strip()
                return front
            except Exception:
                pass
        # Fallback: whole file is body
        return {"body": text, "priority": "medium"}
    except Exception as exc:
        log.exception(EV_ALERT, exc, action="read_draft_failed", file=path.name)
        return None


def _should_auto_approve(draft: dict, threshold: str) -> bool:
    """Return True if this draft's priority is within the auto-approve threshold."""
    if threshold == "none":
        return False
    if threshold == "all":
        return True
    priority = str(draft.get("priority", "medium")).lower()
    draft_rank     = PRIORITY_RANK.get(priority, 2)
    threshold_rank = PRIORITY_RANK.get(threshold, 1)
    return draft_rank <= threshold_rank


def _move_to(path: Path, dest_dir: Path) -> Path:
    """Move file to dest_dir/, handling name collisions with a timestamp suffix."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():
        ts   = datetime.now(tz=PKT).strftime("%H%M%S")
        dest = dest_dir / f"{path.stem}_{ts}{path.suffix}"
    path.rename(dest)
    return dest


def _infer_domain(filename: str) -> str:
    """
    Guess the domain from the file's parent dir name (best) or filename prefix.
    Convention: DRAFT_<domain>_<rest>.json
    """
    parts = filename.split("_")
    if len(parts) >= 2 and parts[1].lower() in KNOWN_DOMAINS:
        return parts[1].lower()
    return "general"


def _write_pending_marker(claimed: Path, domain: str, draft: dict) -> None:
    """Write a .pending_ JSON marker alongside the claimed file for human review."""
    marker = IN_PROG_DIR / f".pending_{claimed.stem}.json"
    marker.write_text(json.dumps({
        "file":       claimed.name,
        "domain":     domain,
        "priority":   draft.get("priority", "medium"),
        "claimed_at": datetime.now(tz=PKT).isoformat(),
        "status":     "awaiting_human",
        "summary":    str(draft.get("summary", draft.get("title", "")))[:200],
    }, indent=2), encoding="utf-8")


def _remove_pending_marker(stem: str) -> None:
    marker = IN_PROG_DIR / f".pending_{stem}.json"
    if marker.exists():
        marker.unlink()


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def process_file(src: Path, auto_approve_threshold: str) -> str:
    """
    Process one file from Pending_Approval/<domain>/.
    Returns: 'approved' | 'pending' | 'skip' | 'error'
    """
    filename = src.name

    # Check if another agent already claimed it
    if _is_already_claimed(filename):
        return "skip"

    # Atomic claim
    claimed = _try_claim(src)
    if claimed is None:
        return "skip"

    # Parse draft content
    draft = _read_draft(claimed)
    if draft is None:
        log.warning(EV_ALERT, action="unreadable_draft", file=filename)
        return "error"

    # Infer domain from filename (the actual dir is src.parent.name but we might
    # lose that after rename — infer from name convention as fallback)
    domain = src.parent.name if src.parent.name in KNOWN_DOMAINS else _infer_domain(filename)

    # Auto-approve?
    if _should_auto_approve(draft, auto_approve_threshold):
        if _is_dry_run():
            # In dry-run: move back to Pending_Approval (undo the claim), don't approve
            src_dir = PENDING_APPROVAL / domain
            src_dir.mkdir(parents=True, exist_ok=True)
            claimed.rename(src_dir / filename)
            print(f"[approval_watcher] [DRY RUN] Would auto-approve: {filename}  (file returned to Pending_Approval)")
            return "skip"
        dest = _move_to(claimed, APPROVED / domain)
        log.info(EV_ODOO_ACTION, action="auto_approved", file=filename,
                 domain=domain, priority=draft.get("priority"))
        print(f"[approval_watcher] AUTO-APPROVED: {filename}")
        print(f"  -> {dest.relative_to(PROJECT_ROOT)}")
        return "approved"

    # Leave for human — write a marker so the Dashboard shows it
    _write_pending_marker(claimed, domain, draft)
    priority = draft.get("priority", "medium")
    print(f"[approval_watcher] AWAITING HUMAN: {filename}  (priority={priority})")
    log.info(EV_TASK_CREATED, action="pending_human_review",
             file=filename, domain=domain, priority=priority)
    return "pending"


# ---------------------------------------------------------------------------
# Human approval / rejection CLI helpers
# ---------------------------------------------------------------------------

def approve_file(filename: str) -> bool:
    """Move a file from In_Progress/approval_watcher/ to Approved/<domain>/."""
    path = IN_PROG_DIR / filename
    if not path.exists():
        print(f"[ERROR] Not found in In_Progress/approval_watcher/: {filename}")
        print(f"  Tip: run --list to see files awaiting approval")
        return False
    if _is_dry_run():
        print(f"[approval_watcher] [DRY RUN] Would approve: {filename}")
        print(f"  Set DRY_RUN=false to execute.")
        return True
    domain = _infer_domain(filename)
    dest   = _move_to(path, APPROVED / domain)
    _remove_pending_marker(path.stem)
    log.info(EV_ODOO_ACTION, action="human_approved", file=filename, domain=domain)
    print(f"[approval_watcher] APPROVED: {filename}")
    print(f"  -> {dest.relative_to(PROJECT_ROOT)}")
    return True


def reject_file(filename: str) -> bool:
    """Move a file from In_Progress/approval_watcher/ to Rejected/<domain>/."""
    path = IN_PROG_DIR / filename
    if not path.exists():
        print(f"[ERROR] Not found in In_Progress/approval_watcher/: {filename}")
        return False
    if _is_dry_run():
        print(f"[approval_watcher] [DRY RUN] Would reject: {filename}")
        print(f"  Set DRY_RUN=false to execute.")
        return True
    domain = _infer_domain(filename)
    dest   = _move_to(path, REJECTED / domain)
    _remove_pending_marker(path.stem)
    log.info(EV_ODOO_ACTION, action="human_rejected", file=filename, domain=domain)
    print(f"[approval_watcher] REJECTED: {filename}")
    print(f"  -> {dest.relative_to(PROJECT_ROOT)}")
    return True


def list_pending() -> None:
    """Print all files currently awaiting human review."""
    markers = sorted(IN_PROG_DIR.glob(".pending_*.json")) if IN_PROG_DIR.exists() else []
    if not markers:
        print("[approval_watcher] No files awaiting human review.")
        return
    print(f"[approval_watcher] Files awaiting human review ({len(markers)}):\n")
    for m in markers:
        try:
            info = json.loads(m.read_text(encoding="utf-8"))
            print(f"  {info['file']}")
            print(f"    domain={info['domain']}  priority={info['priority']}")
            print(f"    summary={info.get('summary', '(none)')[:80]}")
            print(f"    claimed={info['claimed_at']}")
            print()
        except Exception:
            print(f"  {m.name} (unreadable marker)")


# ---------------------------------------------------------------------------
# Main scan loop
# ---------------------------------------------------------------------------

def scan_once(config: dict, verbose: bool = False) -> dict:
    """Scan all Pending_Approval/<domain>/ folders once. Returns count dict."""
    counts: dict[str, int] = {"approved": 0, "pending": 0, "skip": 0, "error": 0}
    if not PENDING_APPROVAL.exists():
        return counts

    for domain_dir in sorted(PENDING_APPROVAL.iterdir()):
        if not domain_dir.is_dir() or domain_dir.name.startswith("."):
            continue
        files = sorted(domain_dir.glob("*.json")) + sorted(domain_dir.glob("*.md"))
        for src in files:
            if src.name.startswith("."):
                continue
            result = process_file(src, config["auto_approve"])
            counts[result] = counts.get(result, 0) + 1
            if verbose:
                rel = src.relative_to(VAULT)
                print(f"  [{result.upper():8}] {rel}")

    return counts


def watch_loop(config: dict, poll_interval: int, verbose: bool) -> None:
    """Continuously poll Pending_Approval/ for new drafts."""
    mode      = config["mode"]
    threshold = config["auto_approve"]
    print(f"[approval_watcher] Starting")
    print(f"  mode             : {mode}")
    print(f"  auto_approve_below: {threshold}")
    print(f"  poll_interval    : {poll_interval}s")
    print(f"  in_progress_dir  : {IN_PROG_DIR.relative_to(PROJECT_ROOT)}")
    log.info(EV_TASK_CREATED, action="watcher_start",
             mode=mode, auto_approve=threshold, poll=poll_interval)

    while True:
        try:
            counts = scan_once(config, verbose=verbose)
            total = counts["approved"] + counts["pending"] + counts["error"]
            if total > 0:
                ts = datetime.now(tz=PKT).strftime("%H:%M:%S")
                print(f"[approval_watcher] {ts}  "
                      f"approved={counts['approved']}  "
                      f"pending={counts['pending']}  "
                      f"error={counts['error']}")
        except Exception as exc:
            log.exception(EV_ALERT, exc, action="scan_loop_error")
            print(f"[approval_watcher] ERROR in scan loop: {exc}")

        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Approval watcher — LOCAL only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python watchers/approval_watcher.py                    # run daemon
  python watchers/approval_watcher.py --once             # scan once and exit
  python watchers/approval_watcher.py --list             # show pending files
  python watchers/approval_watcher.py --approve FILE.json
  python watchers/approval_watcher.py --reject  FILE.json
        """,
    )
    parser.add_argument("--once",    action="store_true",  help="Scan once and exit")
    parser.add_argument("--list",    action="store_true",  help="List files awaiting human review")
    parser.add_argument("--approve", metavar="FILE",       help="Approve a pending file by name")
    parser.add_argument("--reject",  metavar="FILE",       help="Reject a pending file by name")
    parser.add_argument("--poll",    type=int, default=POLL_INTERVAL,
                        help=f"Poll interval in seconds (default: {POLL_INTERVAL})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print each file processed")
    args = parser.parse_args()

    cfg = _load_config()

    # Cloud guard
    if cfg["mode"] == "cloud":
        print("[approval_watcher] BLOCKED: this watcher is LOCAL-only.")
        print("  Set DEPLOYMENT_MODE=local in your .env.local file.")
        sys.exit(1)

    # Human CLI commands
    if args.list:
        list_pending()
        sys.exit(0)

    if args.approve:
        sys.exit(0 if approve_file(args.approve) else 1)

    if args.reject:
        sys.exit(0 if reject_file(args.reject) else 1)

    # One-shot scan
    if args.once:
        counts = scan_once(cfg, verbose=args.verbose)
        print(f"Done — approved={counts['approved']}  pending={counts['pending']}  "
              f"skip={counts['skip']}  error={counts['error']}")
        sys.exit(0 if counts["error"] == 0 else 1)

    # Daemon
    watch_loop(cfg, poll_interval=args.poll, verbose=args.verbose)
