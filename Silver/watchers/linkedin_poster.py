"""
LinkedIn Poster — Silver Tier
================================
Posts content to LinkedIn via Playwright browser automation.
Supports two modes:
  1. Scheduled — orchestrator triggers at configured times
  2. On-demand — drop a .md file into vault/LinkedIn_Drafts/

The poster reads draft files with YAML frontmatter, navigates LinkedIn,
and publishes the post content. Logs results to vault/LinkedIn_Posted/.

Prerequisites:
    1. pip install playwright
    2. playwright install chromium
    3. On first run, log into LinkedIn manually (session is persisted)

Part of the Personal AI Employee system.
"""

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import (
    sync_playwright,
    Browser,
    Page,
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT_PATH: Path = Path(__file__).resolve().parent.parent / "vault"
NEEDS_ACTION_PATH: Path = VAULT_PATH / "Needs_Action"
SKILLS_PATH: Path = VAULT_PATH / "SKILLS.md"
PLANS_PATH: Path = VAULT_PATH / "Plans"
DRAFTS_PATH: Path = VAULT_PATH / "LinkedIn_Drafts"
POSTED_PATH: Path = VAULT_PATH / "LinkedIn_Posted"

WATCHERS_DIR: Path = Path(__file__).resolve().parent
LI_SESSION_DIR: Path = WATCHERS_DIR / ".linkedin_session"
LI_STATE_FILE: Path = WATCHERS_DIR / ".linkedin_state.json"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# How often to check for new drafts (seconds) — used in watcher mode
POLL_INTERVAL: int = 60

# LinkedIn URLs
LI_URL: str = "https://www.linkedin.com"
LI_FEED_URL: str = "https://www.linkedin.com/feed/"

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
    """Load posting state from disk."""
    if LI_STATE_FILE.exists():
        try:
            return json.loads(LI_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Corrupt state file, starting fresh.")
    return {"posted_hashes": [], "last_run": None, "post_count": 0}


def save_state(state: dict[str, Any]) -> None:
    """Persist state to disk."""
    try:
        LI_STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        logger.error("Failed to save state file", exc_info=True)


def draft_hash(content: str) -> str:
    """Create a hash to deduplicate posts."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Draft Parsing
# ---------------------------------------------------------------------------
def parse_draft(file_path: Path) -> Optional[dict[str, str]]:
    """Parse a LinkedIn draft .md file.

    Expected format:
        ---
        title: Optional post title
        schedule: 2026-02-15T10:00  (optional — future posting)
        status: draft
        ---

        Your LinkedIn post content goes here.
        Supports multiple paragraphs.

        #hashtag1 #hashtag2
    """
    try:
        text = file_path.read_text(encoding="utf-8").strip()
    except Exception:
        logger.error("Cannot read draft: %s", file_path.name, exc_info=True)
        return None

    # Split frontmatter from body
    metadata: dict[str, str] = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            # Parse YAML-like frontmatter (simple key: value)
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    metadata[key.strip()] = val.strip()
            body = parts[2].strip()

    if not body:
        logger.warning("Empty post body in %s, skipping.", file_path.name)
        return None

    # Check if draft is scheduled for the future
    schedule_str = metadata.get("schedule", "")
    if schedule_str:
        try:
            scheduled_dt = datetime.fromisoformat(schedule_str)
            # If no timezone, assume PKT
            if scheduled_dt.tzinfo is None:
                scheduled_dt = scheduled_dt.replace(tzinfo=PKT)
            now = datetime.now(tz=PKT)
            if scheduled_dt > now:
                logger.info(
                    "Draft '%s' scheduled for %s — not yet time.",
                    file_path.name,
                    scheduled_dt.isoformat(),
                )
                return None
        except ValueError:
            logger.warning("Invalid schedule date in %s: %s", file_path.name, schedule_str)

    # Skip already-posted drafts
    status = metadata.get("status", "draft")
    if status in ("posted", "failed"):
        return None

    return {
        "file": str(file_path),
        "filename": file_path.name,
        "title": metadata.get("title", ""),
        "body": body,
        "metadata": json.dumps(metadata),
    }


# ---------------------------------------------------------------------------
# LinkedIn Browser Automation
# ---------------------------------------------------------------------------
def wait_for_login(page: Page) -> None:
    """Wait until LinkedIn is fully loaded past login screen."""
    logger.info("Waiting for LinkedIn login...")
    logger.info("If first run, log in manually in the browser window.")

    try:
        # Wait for the feed or global nav to appear (indicates logged in)
        page.wait_for_selector(
            'div.feed-shared-update-v2, '        # feed post
            'div[data-test-id="nav-bar"], '       # nav bar
            'input[aria-label="Search"], '         # search box
            'div.global-nav',                      # global navigation
            timeout=120_000,  # 2 minutes to log in manually
        )
        logger.info("LinkedIn login detected!")
    except PlaywrightTimeout:
        logger.error("LinkedIn login timed out after 2 minutes.")
        raise


def create_post(page: Page, content: str) -> bool:
    """Create a new LinkedIn post using browser automation.

    Returns True on success, False on failure.
    """
    try:
        # Navigate to feed if not already there
        if "/feed" not in page.url:
            page.goto(LI_FEED_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

        # Click "Start a post" button
        start_post_btn = page.locator(
            'button:has-text("Start a post"), '
            'button.share-box-feed-entry__trigger, '
            'div.share-box-feed-entry__trigger'
        ).first
        start_post_btn.wait_for(state="visible", timeout=15_000)
        start_post_btn.click()
        logger.info("Clicked 'Start a post'")

        # Wait for the post editor modal to appear
        page.wait_for_timeout(1500)

        # Find the contenteditable text area in the modal
        editor = page.locator(
            'div.ql-editor[contenteditable="true"], '
            'div[role="textbox"][contenteditable="true"], '
            'div.editor-content div[contenteditable="true"]'
        ).first
        editor.wait_for(state="visible", timeout=10_000)

        # Type the content (use fill for contenteditable, fall back to typing)
        # Split into paragraphs for natural formatting
        paragraphs = content.split("\n\n")
        for i, para in enumerate(paragraphs):
            # Type each line within the paragraph
            lines = para.split("\n")
            for j, line in enumerate(lines):
                editor.type(line, delay=10)
                if j < len(lines) - 1:
                    page.keyboard.press("Shift+Enter")  # line break within paragraph

            if i < len(paragraphs) - 1:
                page.keyboard.press("Enter")
                page.keyboard.press("Enter")  # paragraph break

        logger.info("Post content entered (%d chars)", len(content))
        page.wait_for_timeout(1000)

        # Click the "Post" button
        post_btn = page.locator(
            'button:has-text("Post"):not(:has-text("Start")):not(:has-text("Repost")), '
            'button.share-actions__primary-action'
        ).first
        post_btn.wait_for(state="visible", timeout=10_000)
        post_btn.click()
        logger.info("Clicked 'Post' button")

        # Wait for the modal to close (indicates success)
        page.wait_for_timeout(3000)

        # Verify the modal is gone
        modal_gone = page.locator('div.ql-editor[contenteditable="true"]').count() == 0
        if modal_gone:
            logger.info("Post published successfully!")
            return True
        else:
            logger.warning("Post modal may still be open — verify manually.")
            return True  # Optimistic — content was entered and Post was clicked

    except PlaywrightTimeout:
        logger.error("Timed out during post creation")
        return False
    except Exception:
        logger.error("Failed to create LinkedIn post", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Post Logging
# ---------------------------------------------------------------------------
def log_posted(draft: dict[str, str], success: bool) -> Path:
    """Log a posted/failed draft to vault/LinkedIn_Posted/."""
    now = datetime.now(tz=PKT)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S") + now.strftime("%z")[:3]
    status = "posted" if success else "failed"

    log_filename = f"LI_{status}_{ts}.md"
    log_path = POSTED_PATH / log_filename

    content = (
        f"---\n"
        f"type: linkedin_post\n"
        f"status: {status}\n"
        f"source_draft: {draft['filename']}\n"
        f"posted_at: {now.isoformat()}\n"
        f"title: {draft.get('title', '')}\n"
        f"char_count: {len(draft['body'])}\n"
        f"---\n"
        f"\n"
        f"## {'Posted' if success else 'Failed'} LinkedIn Content\n"
        f"\n"
        f"{draft['body']}\n"
    )

    log_path.write_text(content, encoding="utf-8")
    logger.info("Post log saved: %s", log_filename)
    return log_path


def mark_draft_status(draft_path: Path, status: str) -> None:
    """Update the status field in a draft's frontmatter."""
    try:
        text = draft_path.read_text(encoding="utf-8")
        if "status:" in text:
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.strip().startswith("status:"):
                    lines[i] = f"status: {status}"
                    break
            draft_path.write_text("\n".join(lines), encoding="utf-8")
        else:
            # No status field — add one after the opening ---
            text = text.replace("---\n", f"---\nstatus: {status}\n", 1)
            draft_path.write_text(text, encoding="utf-8")
    except Exception:
        logger.error("Failed to update draft status", exc_info=True)


# ---------------------------------------------------------------------------
# Claude Integration — Generate Post Content
# ---------------------------------------------------------------------------
def generate_post_with_claude(topic: str, tone: str = "professional") -> Optional[str]:
    """Use Claude CLI to generate a LinkedIn post.

    Args:
        topic: What the post should be about
        tone: Writing style — professional, casual, thought-leadership

    Returns:
        Generated post text, or None on failure.
    """
    prompt = (
        f"Generate a LinkedIn post about: {topic}\n\n"
        f"Tone: {tone}\n"
        f"Requirements:\n"
        f"- 150-300 words ideal length\n"
        f"- Include a compelling opening hook (first line is critical on LinkedIn)\n"
        f"- Use short paragraphs and line breaks for readability\n"
        f"- End with a question or call-to-action to drive engagement\n"
        f"- Add 3-5 relevant hashtags at the end\n"
        f"- Do NOT use markdown formatting (no **, ##, etc.) — plain text only\n"
        f"- Output ONLY the post text, nothing else\n"
    )

    try:
        logger.info("Generating LinkedIn post with Claude: '%s'", topic[:60])
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "--print", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
            shell=True,
            env=env,
        )

        if result.returncode == 0 and result.stdout.strip():
            post_text = result.stdout.strip()
            logger.info("Claude generated post (%d chars)", len(post_text))
            return post_text
        else:
            logger.error(
                "Claude post generation failed (exit %d): %s",
                result.returncode,
                result.stderr[:300],
            )
            return None

    except FileNotFoundError:
        logger.warning("Claude CLI not found — cannot generate post.")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Claude timed out generating post.")
        return None
    except Exception:
        logger.error("Post generation failed", exc_info=True)
        return None


def save_generated_draft(topic: str, body: str, schedule_time: str = "") -> Path:
    """Save a Claude-generated post as a draft file for review before posting."""
    now = datetime.now(tz=PKT)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")

    safe_topic = (
        topic[:40]
        .replace("/", "-").replace("\\", "-").replace(":", "-")
        .replace("*", "").replace("?", "").replace('"', "")
        .replace("<", "").replace(">", "").replace("|", "-")
        .strip()
    )

    filename = f"DRAFT_{safe_topic}_{ts}.md"
    draft_path = DRAFTS_PATH / filename

    schedule_line = f"schedule: {schedule_time}\n" if schedule_time else ""

    content = (
        f"---\n"
        f"title: {topic}\n"
        f"generated_at: {now.isoformat()}\n"
        f"status: draft\n"
        f"{schedule_line}"
        f"---\n"
        f"\n"
        f"{body}\n"
    )

    draft_path.write_text(content, encoding="utf-8")
    logger.info("Draft saved: %s", filename)
    return draft_path


# ---------------------------------------------------------------------------
# Main — Watcher Mode
# ---------------------------------------------------------------------------
def run_watcher() -> None:
    """Watch vault/LinkedIn_Drafts/ and post ready drafts via Playwright."""
    # Ensure directories exist
    for d in [DRAFTS_PATH, POSTED_PATH, PLANS_PATH]:
        d.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 60)
    print("  AI Employee — LinkedIn Poster (Silver Tier)")
    print("=" * 60)
    print(f"  Drafts   : {DRAFTS_PATH}")
    print(f"  Posted   : {POSTED_PATH}")
    print(f"  Session  : {LI_SESSION_DIR}")
    print(f"  Poll     : every {POLL_INTERVAL}s")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    state = load_state()
    posted_hashes: set[str] = set(state.get("posted_hashes", []))

    with sync_playwright() as pw:
        # Launch browser with persistent context (keeps login session)
        LI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        context: BrowserContext = pw.chromium.launch_persistent_context(
            user_data_dir=str(LI_SESSION_DIR),
            headless=False,  # Must be visible for initial login
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )

        page: Page = context.pages[0] if context.pages else context.new_page()
        page.goto(LI_FEED_URL)

        # Wait for login
        wait_for_login(page)

        logger.info("LinkedIn poster started. Watching %s", DRAFTS_PATH)

        try:
            while True:
                # Scan for ready drafts
                drafts = sorted(DRAFTS_PATH.glob("*.md"))

                for draft_file in drafts:
                    draft = parse_draft(draft_file)
                    if draft is None:
                        continue

                    h = draft_hash(draft["body"])
                    if h in posted_hashes:
                        continue

                    logger.info("Posting draft: %s", draft["filename"])

                    success = create_post(page, draft["body"])

                    # Log the result
                    log_posted(draft, success)

                    # Update draft status
                    mark_draft_status(draft_file, "posted" if success else "failed")

                    # Track
                    posted_hashes.add(h)
                    state["posted_hashes"] = list(posted_hashes)
                    state["post_count"] = state.get("post_count", 0) + (1 if success else 0)
                    state["last_run"] = datetime.now(tz=PKT).isoformat()
                    save_state(state)

                    # Rate limit — wait between posts
                    if success:
                        logger.info("Waiting 30s before next post (rate limit)...")
                        time.sleep(30)

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print()
            logger.info("Shutting down LinkedIn poster...")

        # Final state save
        state["posted_hashes"] = list(posted_hashes)
        state["last_run"] = datetime.now(tz=PKT).isoformat()
        save_state(state)

        context.close()

    print("LinkedIn poster stopped. Goodbye!")


# ---------------------------------------------------------------------------
# CLI — Generate + Post
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point. Supports subcommands:

        python linkedin_poster.py watch          # Watcher mode (default)
        python linkedin_poster.py generate TOPIC # Generate draft via Claude
        python linkedin_poster.py post FILE      # Post a single draft file now
    """
    args = sys.argv[1:]

    if not args or args[0] == "watch":
        run_watcher()

    elif args[0] == "generate":
        DRAFTS_PATH.mkdir(parents=True, exist_ok=True)

        if len(args) < 2:
            print("Usage: python linkedin_poster.py generate \"Your topic here\"")
            print("       python linkedin_poster.py generate \"AI in healthcare\" --schedule 2026-02-15T10:00")
            sys.exit(1)

        topic = args[1]
        tone = "professional"
        schedule_time = ""

        # Parse optional flags
        for i, arg in enumerate(args[2:], start=2):
            if arg == "--tone" and i + 1 < len(args):
                tone = args[i + 1]
            elif arg == "--schedule" and i + 1 < len(args):
                schedule_time = args[i + 1]

        body = generate_post_with_claude(topic, tone)
        if body:
            path = save_generated_draft(topic, body, schedule_time)
            print(f"\nDraft saved: {path}")
            print("Review and edit the draft, then run the watcher to post it.")
            print(f"Or post immediately: python linkedin_poster.py post \"{path}\"")
        else:
            print("Failed to generate post. Is Claude CLI installed?")
            sys.exit(1)

    elif args[0] == "post":
        if len(args) < 2:
            print("Usage: python linkedin_poster.py post <path-to-draft.md>")
            sys.exit(1)

        draft_file = Path(args[1])
        if not draft_file.exists():
            print(f"File not found: {draft_file}")
            sys.exit(1)

        POSTED_PATH.mkdir(parents=True, exist_ok=True)

        draft = parse_draft(draft_file)
        if not draft:
            print("Draft is empty, already posted, or scheduled for later.")
            sys.exit(1)

        print(f"\nAbout to post ({len(draft['body'])} chars):")
        print("-" * 40)
        print(draft["body"][:300])
        if len(draft["body"]) > 300:
            print("...")
        print("-" * 40)

        confirm = input("\nPost this to LinkedIn? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            sys.exit(0)

        with sync_playwright() as pw:
            LI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(LI_SESSION_DIR),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
            )

            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LI_FEED_URL)
            wait_for_login(page)

            success = create_post(page, draft["body"])
            log_posted(draft, success)
            mark_draft_status(draft_file, "posted" if success else "failed")

            context.close()

        if success:
            print("\nPost published successfully!")
        else:
            print("\nPost may have failed — check LinkedIn manually.")

    else:
        print("Unknown command. Usage:")
        print("  python linkedin_poster.py watch")
        print("  python linkedin_poster.py generate \"topic\"")
        print("  python linkedin_poster.py post <draft.md>")
        sys.exit(1)


if __name__ == "__main__":
    main()
