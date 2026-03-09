"""
Filesystem Watcher — Bronze Tier
=================================
Monitors vault/Inbox for new file drops and creates corresponding
task files in vault/Needs_Action for downstream AI processing.

Part of the Personal AI Employee system.
"""

import logging
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT_PATH: Path = Path(__file__).resolve().parent.parent / "vault"
INBOX_PATH: Path = VAULT_PATH / "Inbox"
NEEDS_ACTION_PATH: Path = VAULT_PATH / "Needs_Action"
PLANS_PATH: Path = VAULT_PATH / "Plans"
SKILLS_PATH: Path = VAULT_PATH / "SKILLS.md"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_size(size_bytes: int) -> str:
    """Return a human-readable file size string (e.g. '142.9 KB')."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def build_task_content(file_path: Path, now: datetime) -> str:
    """Build the Markdown frontmatter + body for a Needs_Action task file."""
    size_bytes = file_path.stat().st_size
    iso_stamp = now.isoformat()
    human_stamp = now.strftime("%Y-%m-%d %H:%M:%S") + " PKT"

    return (
        f"---\n"
        f"type: file_drop\n"
        f"original_name: {file_path.name}\n"
        f"extension: {file_path.suffix}\n"
        f"size_bytes: {size_bytes}\n"
        f"created_at: {iso_stamp}\n"
        f"status: pending\n"
        f"priority: normal\n"
        f"---\n"
        f"\n"
        f"New file detected in Inbox for processing.\n"
        f"\n"
        f"File dropped at: {human_stamp}\n"
        f"Size: {format_size(size_bytes)}\n"
        f"\n"
        f"Next possible actions (AI Employee will decide):\n"
        f"- Read/extract content\n"
        f"- Move to project archive\n"
        f"- Generate summary\n"
        f"- Flag for human review if suspicious\n"
    )


# ---------------------------------------------------------------------------
# Watchdog Event Handler
# ---------------------------------------------------------------------------
class InboxHandler(FileSystemEventHandler):
    """React to new files created inside vault/Inbox."""

    def on_created(self, event: FileCreatedEvent) -> None:
        # Ignore directory creation events
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # Only process direct children of Inbox (not sub-folders)
        if file_path.parent.resolve() != INBOX_PATH.resolve():
            logger.info("Ignoring non-direct child: %s", file_path)
            return

        logger.info("New file detected: %s", file_path.name)

        try:
            now = datetime.now(tz=PKT)

            # Timestamp safe for filenames: colons replaced with hyphens
            ts_for_filename = now.strftime("%Y-%m-%dT%H-%M-%S") + now.strftime("%z")[:3]

            task_filename = f"FILE_{file_path.name}_{ts_for_filename}.md"
            task_path = NEEDS_ACTION_PATH / task_filename

            content = build_task_content(file_path, now)
            task_path.write_text(content, encoding="utf-8")

            logger.info("Task file created: %s", task_path.name)

            # Silver Tier: trigger Claude to generate a plan
            trigger_claude(task_path)

        except Exception:
            logger.error("Failed to process %s", file_path.name, exc_info=True)


# ---------------------------------------------------------------------------
# Claude Trigger
# ---------------------------------------------------------------------------
def trigger_claude(task_path: Path) -> None:
    """Generate a Plan.md from the task card using Claude CLI."""
    plan_filename = task_path.stem + "_PLAN.md"
    plan_path = PLANS_PATH / plan_filename

    prompt = (
        f"You are the AI Employee. A new task card exists at: {task_path}\n"
        f"Read it and the skills file at: {SKILLS_PATH}\n\n"
        f"Generate a structured plan. Output ONLY the plan markdown, nothing else.\n"
        f"Use YAML frontmatter with: source_task, created_at, status, priority.\n"
        f"Then sections: Summary, Recommended Actions (numbered), "
        f"Deadline Estimate, Notes."
    )

    try:
        logger.info("Triggering Claude to generate plan for: %s", task_path.name)
        result = subprocess.run(
            ["claude", "--print", "--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(VAULT_PATH),
        )

        if result.returncode == 0:
            plan_path.write_text(result.stdout, encoding="utf-8")
            logger.info("Plan created: %s", plan_path.name)
        else:
            logger.error("Claude exit code %d: %s",
                         result.returncode, result.stderr[:500])

    except FileNotFoundError:
        logger.warning("Claude CLI not found — task card saved, no plan generated.")
    except subprocess.TimeoutExpired:
        logger.error("Claude timed out for %s", task_path.name)
    except Exception:
        logger.error("Failed to trigger Claude", exc_info=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    # Ensure required directories exist
    INBOX_PATH.mkdir(parents=True, exist_ok=True)
    NEEDS_ACTION_PATH.mkdir(parents=True, exist_ok=True)
    PLANS_PATH.mkdir(parents=True, exist_ok=True)

    observer = Observer()
    observer.schedule(InboxHandler(), str(INBOX_PATH), recursive=False)
    observer.start()

    print()
    print("=" * 55)
    print("  AI Employee — Filesystem Watcher (Bronze Tier)")
    print("=" * 55)
    print(f"  Watching : {INBOX_PATH}")
    print(f"  Output   : {NEEDS_ACTION_PATH}")
    print("  Press Ctrl+C to stop")
    print("=" * 55)
    print()

    logger.info("Watcher started. Monitoring %s", INBOX_PATH)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        logger.info("Shutting down watcher...")
        observer.stop()

    observer.join()
    print("Watcher stopped. Goodbye!")


if __name__ == "__main__":
    main()
