"""
Orchestrator — Platinum Tier
=============================
Central scheduler that runs daily routines (morning briefing, vault
cleanup, Dashboard update, weekly CEO audit) and launches watchers as
managed subprocesses.

Mode-aware: reads DEPLOYMENT_MODE from config.py / .env.*
  cloud — starts cloud-only watchers (gmail, social_drafter)
           skips Playwright-dependent tasks (LinkedIn poster, etc.)
  local — starts local-only watchers (WhatsApp, Playwright posters,
           approval_watcher, update_merger)

Uses the `schedule` library for cron-like task scheduling.

Scheduled tasks (ALL modes unless noted):
    08:00 PKT daily    — Morning briefing (Claude)
    09:00 PKT daily    — LinkedIn draft generation (LOCAL only)
    10:00 PKT daily    — Needs_Action inbox audit (Ralph Loop / Claude)
    23:00 PKT daily    — Vault cleanup
    23:00 PKT Sunday   — Weekly CEO Briefing audit (Claude + Odoo)
    Every 30 minutes   — Dashboard refresh
    Every 5 minutes    — Watcher health check

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
# Deployment mode
# ---------------------------------------------------------------------------
try:
    from config import cfg as _cfg
    DEPLOYMENT_MODE: str = _cfg.get("DEPLOYMENT_MODE", "local").lower()
except ImportError:
    DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "local").lower()

IS_CLOUD: bool = DEPLOYMENT_MODE == "cloud"
IS_LOCAL: bool = not IS_CLOUD

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

CEO_BRIEFING_SCRIPT: Path = WATCHERS_DIR / "ceo_briefing.py"
RALPH_LOOP_SCRIPT:   Path = PROJECT_ROOT / "ralph_loop.py"

# ---------------------------------------------------------------------------
# Watcher configs split by deployment mode
# ---------------------------------------------------------------------------

# Runs on Oracle Cloud VM only
_CLOUD_WATCHERS: list[dict] = [
    {"name": "gmail_watcher",  "script": str(WATCHERS_DIR / "gmail_watcher.py"),  "enabled": True},
    {"name": "social_drafter", "script": str(WATCHERS_DIR / "social_drafter.py"), "enabled": True},
]

# Runs on local Windows machine only
_LOCAL_WATCHERS: list[dict] = [
    {"name": "whatsapp_watcher",  "script": str(WATCHERS_DIR / "whatsapp_watcher.py"),  "enabled": True},
    {"name": "twitter_poster",    "script": str(WATCHERS_DIR / "twitter_poster.py"),    "enabled": True},
    {"name": "meta_poster",       "script": str(WATCHERS_DIR / "meta_poster.py"),       "enabled": True},
    {"name": "linkedin_poster",   "script": str(WATCHERS_DIR / "linkedin_poster.py"),   "enabled": True},
    {"name": "approval_watcher",  "script": str(WATCHERS_DIR / "approval_watcher.py"),  "enabled": True},
    {"name": "update_merger",     "script": str(WATCHERS_DIR / "update_merger.py"),     "enabled": True},
]

# Runs everywhere
_COMMON_WATCHERS: list[dict] = [
    {"name": "filesystem_watcher", "script": str(WATCHERS_DIR / "filesystem_watcher.py"), "enabled": True},
]

# Active watcher list for this run
WATCHER_CONFIGS: list[dict] = (
    _CLOUD_WATCHERS if IS_CLOUD else _LOCAL_WATCHERS
) + _COMMON_WATCHERS


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

    # Count pending tasks (domain subdirs + flat fallback)
    pending_cards: list[Path] = []
    if NEEDS_ACTION_PATH.exists():
        for entry in NEEDS_ACTION_PATH.iterdir():
            if entry.is_dir():
                pending_cards.extend(entry.glob("*.md"))
            elif entry.suffix == ".md":
                pending_cards.append(entry)
    done_cards = list(DONE_PATH.glob("**/*.md")) if DONE_PATH.exists() else []

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
    """Refresh Dashboard.md system-status sections via DashboardWriter."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from dashboard_writer import DashboardWriter  # local import to avoid circular deps

    now = datetime.now(tz=PKT)

    # Count tasks across ALL domain subdirs (Platinum Tier layout)
    pending_cards: list[Path] = []
    if NEEDS_ACTION_PATH.exists():
        for d in NEEDS_ACTION_PATH.iterdir():
            if d.is_dir():
                pending_cards.extend(d.glob("*.md"))
                pending_cards.extend(d.glob("*.json"))
            elif d.suffix in (".md", ".json"):
                pending_cards.append(d)   # flat fallback

    done_cards: list[Path] = []
    if DONE_PATH.exists():
        for d in DONE_PATH.iterdir():
            if d.is_dir():
                done_cards.extend(d.glob("*.md"))
                done_cards.extend(d.glob("*.json"))
            elif d.suffix in (".md", ".json"):
                done_cards.append(d)

    pending_approval: list[Path] = []
    pa_path = VAULT_PATH / "Pending_Approval"
    if pa_path.exists():
        for d in pa_path.iterdir():
            if d.is_dir():
                pending_approval.extend(d.glob("*.md"))
                pending_approval.extend(d.glob("*.json"))

    # Watcher status lines
    watcher_lines: list[str] = [
        f"- Orchestrator: **Running** (mode={DEPLOYMENT_MODE})",
    ]
    for config in WATCHER_CONFIGS:
        name = config["name"]
        if not config["enabled"]:
            watcher_lines.append(f"- {name}: Disabled")
        else:
            proc = WATCHER_PROCESSES.get(name)
            if proc and proc.poll() is None:
                watcher_lines.append(f"- {name}: **Running** (PID {proc.pid})")
            else:
                watcher_lines.append(f"- {name}: Stopped")

    # High-priority items (scan first 30 cards)
    high_priority: list[str] = []
    for card in pending_cards[:30]:
        try:
            text = card.read_text(encoding="utf-8")
            if "priority: high" in text or '"priority": "high"' in text:
                high_priority.append(f"  - `{card.name}`")
        except Exception:
            pass

    # Build sections and flush via DashboardWriter
    writer = DashboardWriter()
    writer.update_section("System Status", watcher_lines)
    writer.update_section("Task Summary", [
        f"- Pending tasks:     **{len(pending_cards)}**",
        f"- Pending approval:  **{len(pending_approval)}**",
        f"- Completed tasks:   **{len(done_cards)}**",
    ])
    writer.update_section("High-Priority Items",
                          high_priority if high_priority else ["- None"])
    writer.update_section("Folders", [
        "- `vault/Needs_Action/<domain>/`    — incoming task cards",
        "- `vault/In_Progress/<agent>/`      — claimed tasks",
        "- `vault/Pending_Approval/<domain>/`— awaiting human approval",
        "- `vault/Approved/<domain>/`        — approved drafts",
        "- `vault/Done/<domain>/`            — completed tasks",
        "- `vault/Plans/<domain>/`           — AI plans & briefings",
        "- `vault/Updates/`                  — cloud -> local broadcasts",
    ])
    writer.flush_now()

    logger.info(
        "Dashboard updated (pending=%d, approval=%d, done=%d).",
        len(pending_cards), len(pending_approval), len(done_cards),
    )


def scheduled_linkedin_draft() -> None:
    """Generate a LinkedIn post draft via Claude at the scheduled time.

    LOCAL only — Playwright-based posting requires local browser sessions.
    Creates a draft in vault/LinkedIn_Drafts/ for human review.
    The linkedin_poster watcher (if running) will post it once approved.
    """
    if IS_CLOUD:
        logger.debug("scheduled_linkedin_draft: skipped on cloud mode.")
        return

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


def weekly_ceo_audit() -> None:
    """Run the weekly CEO Briefing audit every Sunday at 23:00 PKT.

    Calls ceo_briefing.py which:
      1. Reads vault/Business_Goals.md (revenue targets, KPI rules)
      2. Pulls live data from Odoo (invoices, payments, vendor bills)
      3. Runs subscription audit against approved list
      4. Reads social media summaries from vault/Plans/
      5. Calls Claude to generate the Monday Morning CEO Briefing
      6. Saves to vault/Plans/CEO_BRIEFING_<date>.md
    """
    now = datetime.now(tz=PKT)
    date_str = now.strftime("%Y-%m-%d")
    output_path = PLANS_PATH / f"CEO_BRIEFING_{date_str}.md"

    if output_path.exists():
        logger.info("CEO Briefing already exists for %s, skipping.", date_str)
        return

    if not CEO_BRIEFING_SCRIPT.exists():
        logger.error("ceo_briefing.py not found at: %s", CEO_BRIEFING_SCRIPT)
        return

    logger.info("Starting weekly CEO audit for week ending %s...", date_str)

    try:
        result = subprocess.run(
            [sys.executable, str(CEO_BRIEFING_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=360,           # 6 minutes — Odoo + Claude can be slow
            cwd=str(PROJECT_ROOT),
        )

        if result.returncode == 0:
            logger.info("CEO Briefing completed successfully: CEO_BRIEFING_%s.md", date_str)
        else:
            stderr_preview = result.stderr[:500] if result.stderr else "(no output)"
            logger.error(
                "CEO Briefing script failed (exit %d): %s",
                result.returncode,
                stderr_preview,
            )

    except FileNotFoundError:
        logger.error("Python interpreter not found for CEO Briefing subprocess.")
    except subprocess.TimeoutExpired:
        logger.error("CEO Briefing timed out after 360s.")
    except Exception:
        logger.error("CEO Briefing failed", exc_info=True)


def run_ralph_loop(
    task: str,
    done_type: str,
    label: str,
    done_glob: str = "",
    done_path: str = "",
    done_count: int = 1,
    max_iter: int = 15,
    batch: int = 4,
) -> None:
    """
    Launch a ralph_loop.py external loop as a subprocess.

    Used by the orchestrator to kick off autonomous multi-step tasks.
    The loop calls `claude --print` repeatedly until the completion
    signal is satisfied or max_iter is reached.

    Args:
        task:      Full task description sent to Claude.
        done_type: Completion signal type (signal_file / empty_dir /
                   all_handled / file_count).
        label:     Short label (used in log filenames).
        done_glob: Glob pattern for signal_file mode.
        done_path: Directory path for other modes.
        done_count: Required count for file_count mode.
        max_iter:  Maximum Claude iterations before giving up.
        batch:     Items per Claude iteration.
    """
    if not RALPH_LOOP_SCRIPT.exists():
        logger.error("ralph_loop.py not found at: %s", RALPH_LOOP_SCRIPT)
        return

    args = [
        sys.executable, str(RALPH_LOOP_SCRIPT),
        "--task",     task,
        "--done-type", done_type,
        "--label",    label,
        "--max-iter", str(max_iter),
        "--batch",    str(batch),
    ]
    if done_glob:
        args += ["--done-glob", done_glob]
    if done_path:
        args += ["--done-path", done_path]
    if done_count != 1:
        args += ["--done-count", str(done_count)]

    logger.info("Launching ralph_loop: label=%s  done_type=%s", label, done_type)

    try:
        log_file = LOG_DIR / f"ralph_{label}.log"
        proc = subprocess.Popen(
            args,
            stdout=open(log_file, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        logger.info("Ralph loop started: PID %d → log: %s", proc.pid, log_file.name)
    except Exception:
        logger.error("Failed to launch ralph_loop", exc_info=True)


def daily_needs_action_audit() -> None:
    """
    Ralph Loop task — runs daily at 10:00 PKT.
    Autonomously processes all cards in vault/Needs_Action/:
      - Classifies each card (actionable / informational / spam)
      - Creates Plan.md for actionable items
      - Sets status: archived on informational/spam
      - Writes completion signal when all cards are handled
    """
    now = datetime.now(tz=PKT)
    today = now.strftime("%Y-%m-%d")
    signal_glob = f"vault/Plans/NEEDS_ACTION_COMPLETE_{today}.md"

    # Skip if signal already exists
    from pathlib import Path as _Path
    signal_matches = list(
        (VAULT_PATH / "Plans").glob(f"NEEDS_ACTION_COMPLETE_{today}.md")
    )
    if signal_matches:
        logger.info("Needs_Action audit already complete for %s, skipping.", today)
        return

    # Collect cards from domain subdirs (Platinum) + flat root (Gold fallback)
    pending: list[Path] = []
    if NEEDS_ACTION_PATH.exists():
        for entry in NEEDS_ACTION_PATH.iterdir():
            if entry.is_dir():
                pending.extend(entry.glob("*.md"))
            elif entry.suffix == ".md":
                pending.append(entry)

    if not pending:
        logger.info("No Needs_Action cards to process.")
        return

    logger.info("Starting daily Needs_Action audit (%d cards)...", len(pending))

    task = (
        f"You are the AI Employee processing the daily inbox audit. Today is {today}.\n\n"
        f"TASK: Process every .md file in vault/Needs_Action/. For each card:\n\n"
        f"1. Read the card using the Read tool.\n"
        f"2. Classify it into one of:\n"
        f"   - ACTIONABLE: Requires a response or creates work (client email, security issue,\n"
        f"     important request, task to complete)\n"
        f"   - INFORMATIONAL: News, newsletters, updates — no action needed\n"
        f"   - SPAM: Marketing, promotions, irrelevant\n\n"
        f"3. For ACTIONABLE cards:\n"
        f"   - Update the card's frontmatter: status: in_progress\n"
        f"   - Create vault/Plans/PLAN_<card_filename>.md with:\n"
        f"     * Summary of what the card is about\n"
        f"     * Recommended action (reply / schedule / investigate / escalate)\n"
        f"     * Suggested reply text if it's an email\n"
        f"     * Priority: high / medium / low\n"
        f"     * Deadline estimate\n\n"
        f"4. For INFORMATIONAL and SPAM cards:\n"
        f"   - Update the card's frontmatter: status: archived\n"
        f"   - No plan file needed\n\n"
        f"5. WHEN ALL CARDS ARE PROCESSED:\n"
        f"   - Write the file: vault/Plans/NEEDS_ACTION_COMPLETE_{today}.md\n"
        f"   - Content: type: needs_action_complete\\ndate: {today}\\n"
        f"     cards_processed: <count>\\ncards_actionable: <count>\\n"
        f"     cards_archived: <count>\n\n"
        f"Cards to process: {len(pending)}\n"
        f"File list: {', '.join(p.name for p in pending[:20])}\n"
    )

    run_ralph_loop(
        task=task,
        done_type="signal_file",
        done_glob=signal_glob,
        label=f"needs-action-{today}",
        max_iter=max(len(pending) // 3 + 3, 8),   # Scale with card count
        batch=3,
    )


def vault_cleanup() -> None:
    """Move completed/archived task cards older than 7 days from Needs_Action to Done.

    Handles both the flat layout (legacy Gold Tier *.md in Needs_Action/)
    and the domain-namespaced layout (Platinum Tier Needs_Action/<domain>/*.md).
    """
    if not NEEDS_ACTION_PATH.exists():
        return

    DONE_PATH.mkdir(parents=True, exist_ok=True)
    now   = datetime.now(tz=PKT)
    moved = 0

    def _try_move(card: Path, domain: str) -> None:
        nonlocal moved
        try:
            text = card.read_text(encoding="utf-8")
            if "status: completed" not in text and "status: archived" not in text:
                return
            mtime    = datetime.fromtimestamp(card.stat().st_mtime, tz=PKT)
            age_days = (now - mtime).days
            if age_days >= 7:
                dest_dir = DONE_PATH / domain
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / card.name
                card.rename(dest)
                moved += 1
                logger.debug("Vault cleanup: moved %s -> Done/%s/", card.name, domain)
        except Exception:
            logger.error("Cleanup error for %s", card.name, exc_info=True)

    # Scan domain subdirs (Platinum layout)
    for entry in NEEDS_ACTION_PATH.iterdir():
        if entry.is_dir():
            for card in list(entry.glob("*.md")) + list(entry.glob("*.json")):
                _try_move(card, domain=entry.name)
        elif entry.suffix in (".md", ".json"):
            # Legacy flat card — move to Done/general/
            _try_move(entry, domain="general")

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

    # Daily Needs_Action inbox audit — 10:00 PKT via Ralph Loop
    # Classifies all pending cards, creates plans, archives noise
    schedule.every().day.at("10:00").do(daily_needs_action_audit)

    # Weekly CEO Briefing audit — every Sunday at 23:00 PKT
    # Runs after vault_cleanup so the vault is tidy before the audit
    schedule.every().sunday.at("23:00").do(weekly_ceo_audit)

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
    print("  AI Employee — Orchestrator (Platinum Tier)")
    print("=" * 60)
    print(f"  Mode     : {DEPLOYMENT_MODE.upper()}{'  (cloud — limited schedule)' if IS_CLOUD else '  (local — full schedule)'}")
    print(f"  Project  : {PROJECT_ROOT}")
    print(f"  Vault    : {VAULT_PATH}")
    print(f"  Logs     : {LOG_DIR}")
    print(f"  Watchers : {len(WATCHER_CONFIGS)} active")
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
