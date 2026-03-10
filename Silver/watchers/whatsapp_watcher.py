"""
WhatsApp Watcher — Silver Tier
================================
Monitors WhatsApp Web via Playwright for new messages, creates task
cards in vault/Needs_Action, and triggers Claude for AI processing.

Prerequisites:
    1. pip install playwright
    2. playwright install chromium
    3. On first run, scan the QR code with your phone

Part of the Personal AI Employee system.
"""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright, Browser, Page, BrowserContext

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT_PATH: Path = Path(__file__).resolve().parent.parent / "vault"
NEEDS_ACTION_PATH: Path = VAULT_PATH / "Needs_Action"
SKILLS_PATH: Path = VAULT_PATH / "SKILLS.md"
PLANS_PATH: Path = VAULT_PATH / "Plans"

WATCHERS_DIR: Path = Path(__file__).resolve().parent
WA_SESSION_DIR: Path = WATCHERS_DIR / ".whatsapp_session"
WA_STATE_FILE: Path = WATCHERS_DIR / ".whatsapp_last_check.json"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# How often to scrape new messages (seconds)
POLL_INTERVAL: int = 30

# WhatsApp Web URL
WA_URL: str = "https://web.whatsapp.com"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State Tracking
# ---------------------------------------------------------------------------
def load_state() -> dict[str, Any]:
    """Load last-check state from disk."""
    if WA_STATE_FILE.exists():
        try:
            return json.loads(WA_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Corrupt state file, starting fresh.")
    return {"processed_hashes": [], "last_run": None}


def save_state(state: dict[str, Any]) -> None:
    """Persist state to disk."""
    try:
        WA_STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        logger.error("Failed to save state file", exc_info=True)


def message_hash(sender: str, text: str, timestamp: str) -> str:
    """Create a simple hash to deduplicate messages."""
    import hashlib
    raw = f"{sender}|{text}|{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# WhatsApp Web Scraping
# ---------------------------------------------------------------------------
def wait_for_login(page: Page) -> None:
    """Wait until WhatsApp Web is fully loaded (past QR code screen)."""
    logger.info("Waiting for WhatsApp Web login...")
    logger.info("If this is your first run, scan the QR code with your phone.")

    # Wait for the main chat list panel to appear (indicates successful login)
    # The side panel with chat list uses a specific aria-label
    page.wait_for_selector(
        'div[aria-label="Chat list"], #pane-side',
        timeout=120_000,  # 2 minutes to scan QR
    )
    logger.info("WhatsApp Web logged in successfully!")


def get_unread_chats(page: Page) -> list[dict[str, str]]:
    """Scrape unread chat previews from the WhatsApp sidebar.

    Returns a list of dicts: {sender, preview, unread_count, timestamp}
    """
    unread_chats: list[dict[str, str]] = []

    try:
        # Find chat list items with unread badges
        # WhatsApp marks unread chats with a span containing the unread count
        chat_items = page.query_selector_all(
            '#pane-side div[role="listitem"], #pane-side div[data-testid="cell-frame-container"]'
        )

        for item in chat_items:
            try:
                # Check for unread badge (a span with unread count)
                badge = item.query_selector(
                    'span[data-testid="icon-unread-count"], '
                    'span[aria-label*="unread message"]'
                )
                if not badge:
                    continue

                unread_count = badge.inner_text().strip()
                if not unread_count or unread_count == "0":
                    continue

                # Extract sender name (chat title)
                title_el = item.query_selector(
                    'span[data-testid="cell-frame-title"] span, '
                    'span[title]'
                )
                sender = title_el.get_attribute("title") if title_el else ""
                if not sender:
                    sender = title_el.inner_text().strip() if title_el else "(unknown)"

                # Extract message preview
                preview_el = item.query_selector(
                    'span[data-testid="last-msg-status"] span, '
                    'div[data-testid="cell-frame-secondary"] span span'
                )
                preview = preview_el.inner_text().strip() if preview_el else "(no preview)"

                # Extract timestamp
                time_el = item.query_selector(
                    'div[data-testid="cell-frame-primary-detail"] span'
                )
                timestamp = time_el.inner_text().strip() if time_el else ""

                unread_chats.append({
                    "sender": sender,
                    "preview": preview[:300],
                    "unread_count": unread_count,
                    "timestamp": timestamp,
                })

            except Exception:
                # Skip individual chat items that fail to parse
                continue

    except Exception:
        logger.error("Failed to scrape unread chats", exc_info=True)

    return unread_chats


# ---------------------------------------------------------------------------
# Priority Detection
# ---------------------------------------------------------------------------
URGENT_KEYWORDS: set[str] = {
    "urgent", "asap", "emergency", "help", "deadline",
    "important", "call me", "right now", "immediately",
}


def detect_priority(sender: str, preview: str) -> str:
    """Return 'high', 'normal', or 'low' based on keyword detection."""
    combined = f"{sender} {preview}".lower()
    for keyword in URGENT_KEYWORDS:
        if keyword in combined:
            return "high"
    return "normal"


# ---------------------------------------------------------------------------
# Task Card Creation
# ---------------------------------------------------------------------------
def create_task_card(chat: dict[str, str]) -> Path | None:
    """Create a .md task card in Needs_Action for a WhatsApp message."""
    try:
        now = datetime.now(tz=PKT)
        ts_filename = now.strftime("%Y-%m-%dT%H-%M-%S") + now.strftime("%z")[:3]

        # Sanitize sender for filename
        safe_sender = (
            chat["sender"][:40]
            .replace("/", "-")
            .replace("\\", "-")
            .replace(":", "-")
            .replace("*", "")
            .replace("?", "")
            .replace('"', "")
            .replace("<", "")
            .replace(">", "")
            .replace("|", "-")
            .strip()
        )

        task_filename = f"WHATSAPP_{safe_sender}_{ts_filename}.md"
        task_path = NEEDS_ACTION_PATH / task_filename

        priority = detect_priority(chat["sender"], chat["preview"])

        is_group = "+" not in chat["sender"] and len(chat["sender"]) > 2

        content = (
            f"---\n"
            f"type: whatsapp_message\n"
            f"source: whatsapp_web\n"
            f"sender: {chat['sender']}\n"
            f"is_group: {str(is_group).lower()}\n"
            f"unread_count: {chat['unread_count']}\n"
            f"wa_timestamp: {chat['timestamp']}\n"
            f"created_at: {now.isoformat()}\n"
            f"status: pending\n"
            f"priority: {priority}\n"
            f"---\n"
            f"\n"
            f"New WhatsApp message detected.\n"
            f"\n"
            f"From: {chat['sender']}\n"
            f"Unread messages: {chat['unread_count']}\n"
            f"Time: {chat['timestamp']}\n"
            f"\n"
            f"Preview:\n"
            f"> {chat['preview']}\n"
            f"\n"
            f"Next possible actions (AI Employee will decide):\n"
            f"- Read full conversation context\n"
            f"- Draft a reply suggestion\n"
            f"- Extract action items or deadlines\n"
            f"- Summarize if group chat with many messages\n"
            f"- Flag for human review if sensitive\n"
        )

        task_path.write_text(content, encoding="utf-8")
        logger.info("Task card created: %s [priority=%s]", task_filename, priority)
        return task_path

    except Exception:
        logger.error("Failed to create task card for chat: %s",
                      chat.get("sender", "unknown"), exc_info=True)
        return None


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
            logger.error("Claude returned exit code %d: %s",
                         result.returncode, result.stderr[:500])

    except FileNotFoundError:
        logger.warning("Claude CLI not found — task card saved, no plan generated.")
    except subprocess.TimeoutExpired:
        logger.error("Claude timed out processing %s", task_path.name)
    except Exception:
        logger.error("Failed to trigger Claude", exc_info=True)


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
def main() -> None:
    # Ensure directories exist
    NEEDS_ACTION_PATH.mkdir(parents=True, exist_ok=True)
    PLANS_PATH.mkdir(parents=True, exist_ok=True)
    WA_SESSION_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 55)
    print("  AI Employee — WhatsApp Watcher (Silver Tier)")
    print("=" * 55)
    print(f"  Output   : {NEEDS_ACTION_PATH}")
    print(f"  Poll     : every {POLL_INTERVAL}s")
    print(f"  Session  : {WA_SESSION_DIR}")
    print("  Press Ctrl+C to stop")
    print("=" * 55)
    print()

    state = load_state()
    processed_hashes: set[str] = set(state.get("processed_hashes", []))

    with sync_playwright() as pw:
        # Launch browser with persistent context (keeps login session)
        context: BrowserContext = pw.chromium.launch_persistent_context(
            user_data_dir=str(WA_SESSION_DIR),
            headless=False,  # Must be visible for QR scan
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )

        page: Page = context.pages[0] if context.pages else context.new_page()
        page.goto(WA_URL)

        # Wait for login (QR scan on first run, auto-login after)
        wait_for_login(page)

        logger.info("WhatsApp watcher started. Polling every %ds...", POLL_INTERVAL)

        try:
            while True:
                unread = get_unread_chats(page)

                if not unread:
                    logger.info("No new unread chats.")
                else:
                    logger.info("Found %d unread chat(s).", len(unread))

                for chat in unread:
                    h = message_hash(chat["sender"], chat["preview"], chat["timestamp"])

                    if h in processed_hashes:
                        continue

                    logger.info("Processing: [%s] %s",
                                chat["sender"], chat["preview"][:60])

                    task_path = create_task_card(chat)
                    if task_path:
                        trigger_claude(task_path)

                    processed_hashes.add(h)

                # Persist state periodically
                state["processed_hashes"] = list(processed_hashes)
                state["last_run"] = datetime.now(tz=PKT).isoformat()
                save_state(state)

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print()
            logger.info("Shutting down WhatsApp watcher...")

        # Final state save
        state["processed_hashes"] = list(processed_hashes)
        state["last_run"] = datetime.now(tz=PKT).isoformat()
        save_state(state)

        context.close()

    print("WhatsApp watcher stopped. Goodbye!")


if __name__ == "__main__":
    main()
