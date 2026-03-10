"""
dashboard_writer.py — Platinum Tier
======================================
Single-writer, thread-safe manager for vault/Dashboard.md.

Problem
-------
Multiple agents (orchestrator, ralph, watchers) all want to update
Dashboard.md. If they all write simultaneously → corrupted file or
lost updates.

Solution
--------
One DashboardWriter instance runs a background flush thread.
All agents call `writer.update_section(name, lines)` — this puts
an update into a queue.  The flush thread drains the queue and
performs ONE atomic write per flush cycle, rebuilding the full file.

  ┌──────────────┐    update_section()   ┌──────────────┐
  │  orchestrator│ ─────────────────────▶│              │
  │  ralph_loop  │ ─────────────────────▶│  write_queue │──▶ flush_thread ──▶ Dashboard.md
  │  gmail_watch │ ─────────────────────▶│              │
  └──────────────┘                       └──────────────┘

Merge strategy for Git sync
---------------------------
Dashboard.md uses the "local wins" strategy in vault_sync.sh.
This file's content is always generated fresh from current system
state — so whichever machine ran last wins, which is correct.

Usage (standalone)
------------------
    from scripts.dashboard_writer import DashboardWriter

    writer = DashboardWriter()
    writer.start()

    writer.update_section("System Status", [
        "- Orchestrator: **Running**",
        "- ralph_loop: **Running**",
    ])
    writer.update_section("Task Summary", [
        "- Pending tasks: **5**",
        "- Completed tasks: **12**",
    ])

    # On shutdown:
    writer.stop()

Usage (as context manager)
--------------------------
    with DashboardWriter() as w:
        w.update_section("System Status", ["- Orchestrator: Running"])
"""

import queue
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from audit_logger import AuditLogger, EV_START, EV_STOP, EV_ALERT

VAULT         = PROJECT_ROOT / "vault"
DASHBOARD     = VAULT / "Dashboard.md"
PKT           = timezone(timedelta(hours=5))
FLUSH_INTERVAL = 5       # seconds between flush cycles
SECTION_ORDER  = [       # controls top-to-bottom order in Dashboard.md
    "System Status",
    "Task Summary",
    "High-Priority Items",
    "In Progress",
    "Pending Approval",
    "Recent Completions",
    "Sync Status",
    "Folders",
    "Health",
]


# ---------------------------------------------------------------------------
# DashboardWriter
# ---------------------------------------------------------------------------

class DashboardWriter:
    """
    Thread-safe single-writer for Dashboard.md.

    Maintains an in-memory dict of sections (name → list[str] of lines).
    A background thread flushes all pending updates to disk every
    FLUSH_INTERVAL seconds.
    """

    def __init__(self, flush_interval: int = FLUSH_INTERVAL) -> None:
        self._log            = AuditLogger("dashboard_writer")
        self._flush_interval = flush_interval
        self._lock           = threading.Lock()          # protects _sections
        self._dirty          = threading.Event()         # signals pending update
        self._stop_evt       = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Section store: name → list of content lines
        self._sections: dict[str, list[str]] = self._load_existing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_section(self, section: str, lines: list[str]) -> None:
        """
        Replace the content of a named section.

        Args:
            section: Section heading without the ## prefix, e.g. "System Status"
            lines:   List of markdown lines (no trailing newlines needed)
        """
        with self._lock:
            self._sections[section] = lines
        self._dirty.set()

    def get_section(self, section: str) -> list[str]:
        """Read current content of a section (thread-safe)."""
        with self._lock:
            return list(self._sections.get(section, []))

    def flush_now(self) -> None:
        """Force an immediate write to disk (blocks until done)."""
        self._write_dashboard()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> "DashboardWriter":
        self._log.info(EV_START, component="dashboard_writer")
        self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="DashboardFlush")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=10)
        # Final flush
        self._write_dashboard()
        self._log.info(EV_STOP, component="dashboard_writer")

    # Context manager support
    def __enter__(self) -> "DashboardWriter":
        return self.start()

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Internal: flush loop
    # ------------------------------------------------------------------

    def _flush_loop(self) -> None:
        while not self._stop_evt.is_set():
            # Wait for a dirty signal or timeout
            triggered = self._dirty.wait(timeout=self._flush_interval)
            if triggered or not self._stop_evt.is_set():
                self._dirty.clear()
                self._write_dashboard()

    # ------------------------------------------------------------------
    # Internal: write Dashboard.md
    # ------------------------------------------------------------------

    def _write_dashboard(self) -> None:
        try:
            now = datetime.now(tz=PKT)
            ts  = now.strftime("%Y-%m-%d %H:%M:%S PKT")

            lines: list[str] = [
                "# AI Employee Dashboard",
                "",
                "> Platinum Tier — Personal AI Employee",
                f">",
                f"> Last updated: {ts}",
                "",
            ]

            with self._lock:
                sections_snapshot = dict(self._sections)

            # Write sections in preferred order, then any extras
            written = set()
            for name in SECTION_ORDER:
                if name in sections_snapshot:
                    lines += self._render_section(name, sections_snapshot[name])
                    written.add(name)

            for name, content in sections_snapshot.items():
                if name not in written:
                    lines += self._render_section(name, content)

            content = "\n".join(lines) + "\n"

            # Atomic write: write to temp file then rename
            tmp = DASHBOARD.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(DASHBOARD)

        except Exception as exc:
            self._log.exception(EV_ALERT, exc, action="dashboard_write_failed")

    @staticmethod
    def _render_section(name: str, lines: list[str]) -> list[str]:
        return [f"## {name}", ""] + lines + [""]

    # ------------------------------------------------------------------
    # Internal: parse existing Dashboard.md into sections
    # ------------------------------------------------------------------

    def _load_existing(self) -> dict[str, list[str]]:
        """Parse current Dashboard.md so we don't lose data on restart."""
        sections: dict[str, list[str]] = {}

        if not DASHBOARD.exists():
            return self._default_sections()

        current_section: Optional[str] = None
        buf: list[str] = []

        for raw in DASHBOARD.read_text(encoding="utf-8").splitlines():
            if raw.startswith("## "):
                if current_section is not None:
                    # Strip trailing blank lines from previous section
                    sections[current_section] = _strip_trailing_blanks(buf)
                current_section = raw[3:].strip()
                buf = []
            elif raw.startswith("# ") or raw.startswith("> "):
                # Header / metadata lines — skip
                pass
            else:
                if current_section is not None:
                    buf.append(raw)

        if current_section is not None:
            sections[current_section] = _strip_trailing_blanks(buf)

        return sections if sections else self._default_sections()

    @staticmethod
    def _default_sections() -> dict[str, list[str]]:
        return {
            "System Status": [
                "- Orchestrator: **Unknown** (just started)",
            ],
            "Task Summary": [
                "- Pending tasks: **0**",
                "- Completed tasks: **0**",
            ],
            "Folders": [
                "- `vault/Needs_Action/<domain>/` — incoming tasks",
                "- `vault/In_Progress/<agent>/` — claimed tasks",
                "- `vault/Pending_Approval/<domain>/` — awaiting approval",
                "- `vault/Done/<domain>/` — completed",
                "- `vault/Plans/<domain>/` — AI plans",
                "- `vault/Updates/` — broadcast updates",
            ],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_trailing_blanks(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# CLI: write a section from command line (for cron / shell scripts)
#
#   python scripts/dashboard_writer.py "System Status" \
#       "- Orchestrator: Running" "- ralph_loop: Running"
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update one Dashboard.md section")
    parser.add_argument("section", help="Section name (e.g. 'System Status')")
    parser.add_argument("lines",   nargs="+", help="Content lines for the section")
    args = parser.parse_args()

    writer = DashboardWriter()
    writer.update_section(args.section, args.lines)
    writer.flush_now()
    print(f"Updated section '{args.section}' in {DASHBOARD}")
