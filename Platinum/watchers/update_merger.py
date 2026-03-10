"""
update_merger.py — Platinum Tier (LOCAL only)
=============================================
Polls vault/Updates/ for JSON event files placed by cloud agents
after each Git sync. Merges them into Dashboard.md's "Recent Updates"
section via DashboardWriter, then archives each processed file.

Archive lifecycle:
  • Processed files → vault/Updates/.archive/<filename>.json
  • Archive files older than ARCHIVE_TTL_H hours are deleted automatically

Runs as a daemon (default) or one-shot (--once).

Usage:
  python watchers/update_merger.py              # continuous daemon
  python watchers/update_merger.py --once       # process current queue and exit
  python watchers/update_merger.py --verbose    # show each file merged
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))   # for dashboard_writer

from audit_logger import AuditLogger, EV_QUEUE_DRAIN, EV_ALERT

VAULT        = PROJECT_ROOT / "vault"
UPDATES_DIR  = VAULT / "Updates"
ARCHIVE_DIR  = UPDATES_DIR / ".archive"

PKT           = timezone(timedelta(hours=5))
POLL_INTERVAL  = 30    # seconds
ARCHIVE_TTL_H  = 48    # hours until archived files are deleted
MAX_DASH_LINES = 20    # max "Recent Updates" lines shown in Dashboard

AGENT_NAME = "update_merger"
log = AuditLogger(AGENT_NAME)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _format_line(update: dict) -> str:
    """Format one update entry as a Dashboard.md bullet line."""
    try:
        ts_utc = datetime.fromisoformat(update["timestamp_utc"].replace("Z", "+00:00"))
        ts_pkt = ts_utc.astimezone(PKT)
        ts_str = ts_pkt.strftime("%m-%d %H:%M")
    except Exception:
        ts_str = "?"

    component = update.get("component", "?")
    domain    = update.get("domain",    "?")
    summary   = update.get("summary",   "(no summary)")
    return f"- `{ts_str}` [{component}/{domain}] {summary}"


# ---------------------------------------------------------------------------
# Archive helpers
# ---------------------------------------------------------------------------

def _archive_file(path: Path) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / path.name
    if dest.exists():
        ts   = datetime.now(tz=timezone.utc).strftime("%H%M%S")
        dest = ARCHIVE_DIR / f"{path.stem}_{ts}{path.suffix}"
    path.rename(dest)


def _cleanup_archive() -> None:
    """Delete archived files older than ARCHIVE_TTL_H hours."""
    if not ARCHIVE_DIR.exists():
        return
    cutoff  = datetime.now(tz=timezone.utc) - timedelta(hours=ARCHIVE_TTL_H)
    deleted = 0
    for f in ARCHIVE_DIR.glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                deleted += 1
        except Exception:
            pass
    if deleted:
        log.info(EV_QUEUE_DRAIN, action="archive_cleaned", deleted=deleted)


# ---------------------------------------------------------------------------
# Core merge
# ---------------------------------------------------------------------------

def merge_once(dashboard_writer=None, verbose: bool = False) -> int:
    """
    Process all pending .json files in vault/Updates/ (ignoring hidden files).
    Prepends new lines to Dashboard "Recent Updates" section.
    Returns count of updates merged.
    """
    if not UPDATES_DIR.exists():
        return 0

    pending = sorted(
        [f for f in UPDATES_DIR.glob("*.json") if not f.name.startswith(".")],
        key=lambda p: p.name,
    )
    if not pending:
        return 0

    new_lines: list[str] = []
    for path in pending:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            line = _format_line(data)
            new_lines.append(line)
            _archive_file(path)
            log.info(EV_QUEUE_DRAIN, action="update_merged", file=path.name)
            if verbose:
                print(f"  [merged] {path.name}")
                print(f"    {line}")
        except Exception as exc:
            log.exception(EV_ALERT, exc, action="merge_failed", file=path.name)
            if verbose:
                print(f"  [ERROR]  {path.name}: {exc}")

    # Update Dashboard "Recent Updates" section — newest at top
    if new_lines and dashboard_writer is not None:
        existing = dashboard_writer.get_section("Recent Updates")
        combined = new_lines + [l for l in existing if l.strip()]
        dashboard_writer.update_section("Recent Updates", combined[:MAX_DASH_LINES])

    _cleanup_archive()
    return len(new_lines)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

def watch_loop(poll_interval: int, verbose: bool) -> None:
    """Continuously poll vault/Updates/ and merge into Dashboard."""
    from dashboard_writer import DashboardWriter

    print(f"[update_merger] Starting  |  poll={poll_interval}s  |  archive_ttl={ARCHIVE_TTL_H}h")
    log.info(EV_QUEUE_DRAIN, action="merger_start", poll=poll_interval)

    with DashboardWriter() as writer:
        while True:
            try:
                count = merge_once(dashboard_writer=writer, verbose=verbose)
                if count > 0:
                    ts = datetime.now(tz=PKT).strftime("%H:%M:%S")
                    print(f"[update_merger] {ts}  merged {count} update(s)")
            except Exception as exc:
                log.exception(EV_ALERT, exc, action="loop_error")
                print(f"[update_merger] ERROR: {exc}")

            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Update merger — LOCAL only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python watchers/update_merger.py               # continuous daemon
  python watchers/update_merger.py --once        # one shot
  python watchers/update_merger.py --once -v     # one shot, verbose
        """,
    )
    parser.add_argument("--once",    action="store_true",
                        help="Process current queue once and exit")
    parser.add_argument("--poll",    type=int, default=POLL_INTERVAL,
                        help=f"Poll interval in seconds (default: {POLL_INTERVAL})")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print each update processed")
    args = parser.parse_args()

    # Cloud guard
    try:
        from config import cfg as _cfg
        if _cfg.get("DEPLOYMENT_MODE", "local").lower() == "cloud":
            print("[update_merger] BLOCKED: update_merger is LOCAL-only.")
            sys.exit(1)
    except ImportError:
        pass

    if args.once:
        from dashboard_writer import DashboardWriter
        writer = DashboardWriter()
        count  = merge_once(dashboard_writer=writer, verbose=args.verbose)
        writer.flush_now()
        print(f"Done — merged {count} update(s)")
        sys.exit(0)

    watch_loop(poll_interval=args.poll, verbose=args.verbose)
