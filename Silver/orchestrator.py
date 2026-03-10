"""
Orchestrator — Silver Tier
===========================
Central scheduler that runs daily routines (morning briefing, vault
cleanup, Dashboard update) and launches watchers as managed subprocesses.

Uses the `schedule` library for cron-like task scheduling.

Prerequisites:
    pip install schedule

Part of the Personal AI Employee system.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import schedule

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent
VAULT_PATH: Path = PROJECT_ROOT / "vault"
NEEDS_ACTION_PATH: Path = VAULT_PATH / "Needs_Action"
DONE_PATH: Path = VAULT_PATH / "Done"
PLANS_PATH: Path = VAULT_PATH / "Plans"
SKILLS_PATH: Path = VAULT_PATH / "SKILLS.md"
DASHBOARD_PATH: Path = VAULT_PATH / "Dashboard.md"
WATCHERS_DIR: Path = PROJECT_ROOT / "watchers"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR: Path = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "orchestrator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Managed Watcher Subprocesses
# ---------------------------------------------------------------------------
WATCHER_PROCESSES: dict[str, subprocess.Popen] = {}

LINKEDIN_DRAFTS_PATH: Path = VAULT_PATH / "LinkedIn_Drafts"

WATCHER_CONFIGS: list[dict] = [
    {
        "name": "filesystem_watcher",
        "script": str(WATCHERS_DIR / "filesystem_watcher.py"),
        "enabled": True,
    },
    {
        "name": "gmail_watcher",
        "script": str(WATCHERS_DIR / "gmail_watcher.py"),
        "enabled": True,
    },
    {
        "name": "whatsapp_watcher",
        "script": str(WATCHERS_DIR / "whatsapp_watcher.py"),
        # Disabled by default — requires manual QR scan on first run.
        # Enable once you've done the initial login.
        "enabled": False,
    },
    {
        "name": "linkedin_poster",
        "script": str(WATCHERS_DIR / "linkedin_poster.py"),
        # Disabled by default — requires manual LinkedIn login on first run.
        # Enable once you've done the initial login.
        "enabled": False,
    },
]


def start_watcher(config: dict) -> Optional[subprocess.Popen]:
    """Launch a watcher as a subprocess and return the Popen handle."""
    name = config["name"]
    script = config["script"]

    if not Path(script).exists():
        logger.warning("Watcher script not found, skipping: %s", script)
        return None

    log_file = LOG_DIR / f"{name}.log"
    logger.info("Starting watcher: %s", name)

    proc = subprocess.Popen(
        [sys.executable, script],
        stdout=open(log_file, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )
    logger.info("  PID %d → log: %s", proc.pid, log_file.name)
    return proc


def start_all_watchers() -> None:
    """Start all enabled watchers that aren't already running."""
    for config in WATCHER_CONFIGS:
        name = config["name"]
        if not config["enabled"]:
            logger.info("Watcher disabled, skipping: %s", name)
            continue

        # Skip if already running
        existing = WATCHER_PROCESSES.get(name)
        if existing and existing.poll() is None:
            continue

        proc = start_watcher(config)
        if proc:
            WATCHER_PROCESSES[name] = proc


def stop_all_watchers() -> None:
    """Gracefully stop all running watcher subprocesses."""
    for name, proc in WATCHER_PROCESSES.items():
        if proc.poll() is None:
            logger.info("Stopping watcher: %s (PID %d)", name, proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Force-killing %s", name)
                proc.kill()
    WATCHER_PROCESSES.clear()


def health_check_watchers() -> None:
    """Restart any watchers that have crashed."""
    for config in WATCHER_CONFIGS:
        name = config["name"]
        if not config["enabled"]:
            continue

        proc = WATCHER_PROCESSES.get(name)
        if proc is None or proc.poll() is not None:
            exit_code = proc.returncode if proc else "never started"
            logger.warning("Watcher '%s' is down (exit=%s). Restarting...", name, exit_code)
            new_proc = start_watcher(config)
            if new_proc:
                WATCHER_PROCESSES[name] = new_proc


# ---------------------------------------------------------------------------
# Scheduled Tasks
# ---------------------------------------------------------------------------
def morning_briefing() -> None:
    """Generate a daily morning briefing via Claude CLI.

    Summarizes pending tasks, overnight emails/messages, and today's priorities.
    Output is saved to vault/Plans/DAILY_BRIEFING_<date>.md.
    """
    now = datetime.now(tz=PKT)
    date_str = now.strftime("%Y-%m-%d")
    briefing_path = PLANS_PATH / f"DAILY_BRIEFING_{date_str}.md"

    # Skip if today's briefing already exists
    if briefing_path.exists():
        logger.info("Morning briefing already exists for %s, skipping.", date_str)
        return

    # Count pending tasks
    pending_cards = list(NEEDS_ACTION_PATH.glob("*.md")) if NEEDS_ACTION_PATH.exists() else []
    done_cards = list(DONE_PATH.glob("*.md")) if DONE_PATH.exists() else []

    # Build a summary of pending card types
    type_counts: dict[str, int] = {}
    for card in pending_cards:
        try:
            text = card.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("type:"):
                    t = line.split(":", 1)[1].strip()
                    type_counts[t] = type_counts.get(t, 0) + 1
                    break
        except Exception:
            pass

    type_summary = ", ".join(f"{v} {k}" for k, v in type_counts.items()) or "none"

    prompt = (
        f"You are the AI Employee. Today is {date_str}.\n"
        f"Generate a concise daily morning briefing.\n\n"
        f"Current status:\n"
        f"- Pending task cards: {len(pending_cards)} ({type_summary})\n"
        f"- Completed tasks (all time): {len(done_cards)}\n"
        f"- Active watchers: filesystem, gmail\n\n"
        f"Read the skills file at: {SKILLS_PATH}\n"
        f"Read any pending cards in: {NEEDS_ACTION_PATH}\n\n"
        f"Output a markdown briefing with sections:\n"
        f"## Daily Briefing — {date_str}\n"
        f"### Overnight Summary\n"
        f"### Today's Priorities\n"
        f"### Pending Actions\n"
        f"### Recommended Focus\n"
    )

    try:
        logger.info("Generating morning briefing for %s...", date_str)
        result = subprocess.run(
            ["claude", "--print", "--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(VAULT_PATH),
        )

        if result.returncode == 0 and result.stdout.strip():
            PLANS_PATH.mkdir(parents=True, exist_ok=True)
            briefing_path.write_text(result.stdout, encoding="utf-8")
            logger.info("Morning briefing saved: %s", briefing_path.name)
        else:
            logger.error("Claude briefing failed (exit %d): %s",
                         result.returncode, result.stderr[:300])

    except FileNotFoundError:
        logger.warning("Claude CLI not found — morning briefing skipped.")
    except subprocess.TimeoutExpired:
        logger.error("Claude timed out generating briefing.")
    except Exception:
        logger.error("Morning briefing failed", exc_info=True)


def update_dashboard() -> None:
    """Refresh vault/Dashboard.md with current system status."""
    now = datetime.now(tz=PKT)

    pending = list(NEEDS_ACTION_PATH.glob("*.md")) if NEEDS_ACTION_PATH.exists() else []
    done = list(DONE_PATH.glob("*.md")) if DONE_PATH.exists() else []

    # Watcher status
    watcher_lines = []
    for config in WATCHER_CONFIGS:
        name = config["name"]
        if not config["enabled"]:
            status = "Disabled"
        else:
            proc = WATCHER_PROCESSES.get(name)
            if proc and proc.poll() is None:
                status = f"**Running** (PID {proc.pid})"
            else:
                status = "Stopped"
        watcher_lines.append(f"- {name}: {status}")

    # High-priority items
    high_priority: list[str] = []
    for card in pending[:20]:
        try:
            text = card.read_text(encoding="utf-8")
            if "priority: high" in text:
                high_priority.append(f"  - `{card.name}`")
        except Exception:
            pass

    hp_section = "\n".join(high_priority) if high_priority else "  - None"

    dashboard = (
        f"# AI Employee Dashboard\n"
        f"\n"
        f"> Silver Tier — Local-first Personal AI Employee\n"
        f">\n"
        f"> Last updated: {now.strftime('%Y-%m-%d %H:%M:%S')} PKT\n"
        f"\n"
        f"## System Status\n"
        f"- Orchestrator: **Running**\n"
        f"{chr(10).join(watcher_lines)}\n"
        f"\n"
        f"## Task Summary\n"
        f"- Pending tasks: **{len(pending)}**\n"
        f"- Completed tasks: **{len(done)}**\n"
        f"\n"
        f"## High-Priority Items\n"
        f"{hp_section}\n"
        f"\n"
        f"## Folders\n"
        f"- `vault/Needs_Action/` — unprocessed task cards\n"
        f"- `vault/Pending_Approval/` — awaiting human approval\n"
        f"- `vault/Done/` — completed tasks\n"
        f"- `vault/Plans/` — AI-generated plans & briefings\n"
    )

    DASHBOARD_PATH.write_text(dashboard, encoding="utf-8")
    logger.info("Dashboard updated (%d pending, %d done).", len(pending), len(done))


def scheduled_linkedin_draft() -> None:
    """Generate a LinkedIn post draft via Claude at the scheduled time.

    Creates a draft in vault/LinkedIn_Drafts/ for human review.
    The linkedin_poster watcher (if running) will post it once approved.
    """
    LINKEDIN_DRAFTS_PATH.mkdir(parents=True, exist_ok=True)

    now = datetime.now(tz=PKT)
    date_str = now.strftime("%Y-%m-%d")

    # Skip if a draft was already generated today
    today_drafts = [
        f for f in LINKEDIN_DRAFTS_PATH.glob("*.md")
        if date_str in f.name and "status: draft" in f.read_text(encoding="utf-8")
    ]
    if today_drafts:
        logger.info("LinkedIn draft already exists for %s, skipping.", date_str)
        return

    prompt = (
        f"You are a professional LinkedIn content creator.\n"
        f"Today is {date_str}.\n"
        f"Generate a thought-leadership LinkedIn post about a trending topic in "
        f"AI, technology, or business productivity.\n\n"
        f"Requirements:\n"
        f"- 150-250 words\n"
        f"- Compelling opening hook\n"
        f"- Short paragraphs, easy to scan on mobile\n"
        f"- End with an engagement question\n"
        f"- Add 3-5 relevant hashtags\n"
        f"- Plain text only, NO markdown formatting\n"
        f"- Output ONLY the post text, nothing else\n"
    )

    try:
        logger.info("Generating scheduled LinkedIn draft...")
        result = subprocess.run(
            ["claude", "--print", "--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0 and result.stdout.strip():
            body = result.stdout.strip()
            ts = now.strftime("%Y-%m-%dT%H-%M-%S")
            filename = f"DRAFT_scheduled_{ts}.md"
            draft_path = LINKEDIN_DRAFTS_PATH / filename

            content = (
                f"---\n"
                f"title: Scheduled daily post\n"
                f"generated_at: {now.isoformat()}\n"
                f"status: draft\n"
                f"---\n"
                f"\n"
                f"{body}\n"
            )
            draft_path.write_text(content, encoding="utf-8")
            logger.info("LinkedIn draft saved: %s (%d chars)", filename, len(body))
        else:
            logger.error("Claude LinkedIn draft failed (exit %d)", result.returncode)

    except FileNotFoundError:
        logger.warning("Claude CLI not found — LinkedIn draft skipped.")
    except subprocess.TimeoutExpired:
        logger.error("Claude timed out generating LinkedIn draft.")
    except Exception:
        logger.error("LinkedIn draft generation failed", exc_info=True)


def vault_cleanup() -> None:
    """Move task cards older than 7 days from Needs_Action to Done.

    Only moves cards whose status is 'completed' or 'archived' in frontmatter.
    """
    if not NEEDS_ACTION_PATH.exists():
        return

    DONE_PATH.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=PKT)
    moved = 0

    for card in NEEDS_ACTION_PATH.glob("*.md"):
        try:
            text = card.read_text(encoding="utf-8")

            # Only auto-move completed/archived cards
            if "status: completed" not in text and "status: archived" not in text:
                continue

            # Check age via file modification time
            mtime = datetime.fromtimestamp(card.stat().st_mtime, tz=PKT)
            age_days = (now - mtime).days

            if age_days >= 7:
                dest = DONE_PATH / card.name
                card.rename(dest)
                moved += 1

        except Exception:
            logger.error("Cleanup error for %s", card.name, exc_info=True)

    if moved:
        logger.info("Vault cleanup: moved %d completed card(s) to Done/.", moved)
    else:
        logger.info("Vault cleanup: nothing to move.")


# ---------------------------------------------------------------------------
# Schedule Setup
# ---------------------------------------------------------------------------
def setup_schedule() -> None:
    """Register all scheduled tasks."""

    # Morning briefing — every day at 08:00 PKT
    schedule.every().day.at("08:00").do(morning_briefing)

    # Dashboard refresh — every 30 minutes
    schedule.every(30).minutes.do(update_dashboard)

    # Vault cleanup — every day at 23:00 PKT
    schedule.every().day.at("23:00").do(vault_cleanup)

    # LinkedIn draft generation — every day at 09:00 PKT
    schedule.every().day.at("09:00").do(scheduled_linkedin_draft)

    # Watcher health check — every 5 minutes
    schedule.every(5).minutes.do(health_check_watchers)

    logger.info("Scheduled tasks registered:")
    for job in schedule.get_jobs():
        logger.info("  → %s", job)


# ---------------------------------------------------------------------------
# Graceful Shutdown
# ---------------------------------------------------------------------------
def handle_shutdown(signum, frame):
    """Handle SIGTERM/SIGINT for clean shutdown."""
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else signum
    logger.info("Received %s — shutting down...", sig_name)
    stop_all_watchers()
    schedule.clear()
    logger.info("Orchestrator stopped. Goodbye!")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # Ensure directories exist
    for d in [NEEDS_ACTION_PATH, DONE_PATH, PLANS_PATH, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 60)
    print("  AI Employee — Orchestrator (Silver Tier)")
    print("=" * 60)
    print(f"  Project  : {PROJECT_ROOT}")
    print(f"  Vault    : {VAULT_PATH}")
    print(f"  Logs     : {LOG_DIR}")
    print(f"  Time     : {datetime.now(tz=PKT).strftime('%Y-%m-%d %H:%M:%S')} PKT")
    print("=" * 60)
    print()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Start watchers
    start_all_watchers()

    # Register scheduled jobs
    setup_schedule()

    # Run an initial dashboard update
    update_dashboard()

    logger.info("Orchestrator running. Waiting for scheduled tasks...")
    logger.info("Next briefing at 08:00 PKT. Dashboard refreshes every 30m.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        logger.info("Keyboard interrupt received.")
        stop_all_watchers()
        schedule.clear()

    logger.info("Orchestrator stopped. Goodbye!")


if __name__ == "__main__":
    main()
