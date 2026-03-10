"""
Twitter/X Poster — Free Edition (Playwright)
=============================================================
Posts content to X (Twitter) via Playwright browser automation.
No API keys required — uses a real browser with persistent session.

Same pattern as whatsapp_watcher.py:
  - First run: browser opens, you log into twitter.com manually
  - Subsequent runs: session restored automatically from saved profile

Follows HITL draft-file pattern:
  - Drop a .md draft into vault/Twitter_Drafts/
  - Human reviews/edits → sets status to "ready"
  - Poster picks it up and posts via browser

Supports:
  - Plain text tweets (up to 280 chars)
  - Tweet threads (multiple tweets in sequence)

Prerequisites:
    pip install playwright python-dotenv
    playwright install chromium

No env vars needed for Twitter — just log in on first run.

Part of the Personal AI Employee system.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, BrowserContext

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT_PATH   = PROJECT_ROOT / "vault"
DRAFTS_PATH  = VAULT_PATH / "Twitter_Drafts"
POSTED_PATH  = VAULT_PATH / "Twitter_Posted"
SESSION_DIR  = VAULT_PATH / ".twitter_session"

WATCHERS_DIR = Path(__file__).resolve().parent
STATE_FILE   = WATCHERS_DIR / ".twitter_state.json"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

POLL_INTERVAL      = 60   # seconds between draft scans
RATE_LIMIT_SECONDS = 30   # seconds between successive posts

TWITTER_URL = "https://x.com"

# ---------------------------------------------------------------------------
# Load env (for any other settings)
# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "twitter_poster.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("twitter_poster")


# ---------------------------------------------------------------------------
# State Tracking (deduplication)
# ---------------------------------------------------------------------------
def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Corrupt state file, starting fresh.")
    return {"posted_hashes": [], "last_run": None, "post_count": 0}


def save_state(state: dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        logger.error("Failed to save state", exc_info=True)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Login Detection
# ---------------------------------------------------------------------------
def wait_for_login(page: Page) -> None:
    """Wait until Twitter/X is fully loaded and user is logged in."""
    logger.info("Waiting for Twitter/X login...")
    logger.info("If this is your first run, log in manually in the browser window.")
    logger.info("You have 10 minutes to log in.")

    # Poll every 5 seconds for up to 10 minutes
    for _ in range(120):
        url = page.url
        # Logged in if URL contains /home or /notifications etc (not login pages)
        if any(x in url for x in ["/home", "/notifications", "/messages", "/explore"]):
            logger.info("Twitter/X logged in successfully!")
            return

        # Also try selector-based detection
        try:
            el = page.query_selector(
                'a[data-testid="AppTabBar_Home_Link"], '
                'a[data-testid="SideNav_NewTweet_Button"], '
                '[data-testid="primaryColumn"]'
            )
            if el:
                logger.info("Twitter/X logged in successfully!")
                return
        except Exception:
            pass

        time.sleep(5)

    raise TimeoutError("Login timeout — user did not log in within 10 minutes.")


# ---------------------------------------------------------------------------
# Posting via Playwright
# ---------------------------------------------------------------------------
def post_tweet(page: Page, text: str) -> dict[str, Any]:
    """Post a single tweet via browser automation."""
    try:
        # Click compose / new tweet button
        page.click('a[data-testid="SideNav_NewTweet_Button"]')
        page.wait_for_timeout(1500)

        # Wait for tweet compose area
        textarea = page.wait_for_selector(
            'div[data-testid="tweetTextarea_0"]',
            timeout=10_000,
        )
        textarea.click()
        page.wait_for_timeout(500)

        # Type the tweet text
        page.keyboard.type(text, delay=20)
        page.wait_for_timeout(1000)

        # Click the Post / Tweet button
        page.click('button[data-testid="tweetButtonInline"]')
        page.wait_for_timeout(3000)

        logger.info("Tweet posted successfully.")
        return {"success": True}

    except Exception as e:
        logger.error("Failed to post tweet: %s", e)
        return {"success": False, "error": str(e)}


def post_thread(page: Page, tweets: list[str]) -> dict[str, Any]:
    """Post a thread — tweets added one by one in the compose window."""
    try:
        # Click compose button
        page.click('a[data-testid="SideNav_NewTweet_Button"]')
        page.wait_for_timeout(1500)

        for i, text in enumerate(tweets):
            if i == 0:
                # First tweet — already in the compose box
                textarea = page.wait_for_selector(
                    'div[data-testid="tweetTextarea_0"]',
                    timeout=10_000,
                )
            else:
                # Click "Add to thread" button
                page.click('button[data-testid="addButton"]')
                page.wait_for_timeout(1000)
                # Focus the new textarea (last one)
                textareas = page.query_selector_all('div[data-testid^="tweetTextarea_"]')
                textarea = textareas[-1] if textareas else None

            if not textarea:
                raise RuntimeError(f"Could not find textarea for tweet #{i + 1}")

            textarea.click()
            page.wait_for_timeout(300)
            page.keyboard.type(text, delay=20)
            page.wait_for_timeout(800)
            logger.info("Thread tweet %d/%d typed.", i + 1, len(tweets))

        # Post entire thread
        page.click('button[data-testid="tweetButtonInline"]')
        page.wait_for_timeout(3000)

        logger.info("Thread posted successfully (%d tweets).", len(tweets))
        return {"success": True}

    except Exception as e:
        logger.error("Failed to post thread: %s", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Draft Parsing
# ---------------------------------------------------------------------------
def parse_draft(file_path: Path) -> Optional[dict[str, Any]]:
    """Parse a Twitter draft .md file.

    Frontmatter fields:
        post_type: tweet | thread       (default: tweet)
        status:    draft | ready | posted | failed
        schedule:  2026-03-01T10:00    (optional)

    For thread type, separate tweets with a line: ---tweet---
    """
    try:
        text = file_path.read_text(encoding="utf-8").strip()
    except Exception:
        logger.error("Cannot read draft: %s", file_path.name, exc_info=True)
        return None

    metadata: dict[str, str] = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    metadata[key.strip()] = val.strip()
            body = parts[2].strip()

    if not body:
        logger.warning("Empty body in %s, skipping.", file_path.name)
        return None

    if metadata.get("status", "draft") != "ready":
        return None

    # Check schedule
    schedule_str = metadata.get("schedule", "")
    if schedule_str:
        try:
            scheduled_dt = datetime.fromisoformat(schedule_str)
            if scheduled_dt.tzinfo is None:
                scheduled_dt = scheduled_dt.replace(tzinfo=PKT)
            if scheduled_dt > datetime.now(tz=PKT):
                logger.info("Draft '%s' scheduled — not yet time.", file_path.name)
                return None
        except ValueError:
            pass

    post_type = metadata.get("post_type", "tweet")
    thread_tweets: list[str] = []
    if post_type == "thread":
        thread_tweets = [t.strip() for t in body.split("---tweet---") if t.strip()]

    return {
        "file":          str(file_path),
        "filename":      file_path.name,
        "body":          body,
        "post_type":     post_type,
        "thread_tweets": thread_tweets,
        "title":         metadata.get("title", ""),
        "metadata":      metadata,
    }


# ---------------------------------------------------------------------------
# Post Logging
# ---------------------------------------------------------------------------
def log_posted(draft: dict, success: bool, error: str = "") -> None:
    """Save post result to vault/Twitter_Posted/."""
    POSTED_PATH.mkdir(parents=True, exist_ok=True)
    now    = datetime.now(tz=PKT)
    ts     = now.strftime("%Y-%m-%dT%H-%M-%S")
    status = "posted" if success else "failed"

    content = (
        f"---\n"
        f"type: twitter_post\n"
        f"status: {status}\n"
        f"source_draft: {draft['filename']}\n"
        f"posted_at: {now.isoformat()}\n"
        f"post_type: {draft['post_type']}\n"
        f"char_count: {len(draft['body'])}\n"
    )
    if error:
        content += f"error: {error}\n"
    content += (
        f"---\n\n"
        f"## {'Posted' if success else 'Failed'}\n\n"
        f"{draft['body'][:500]}\n"
    )

    filename = f"TWITTER_{status}_{ts}.md"
    (POSTED_PATH / filename).write_text(content, encoding="utf-8")
    logger.info("Post log saved: %s", filename)


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
    except Exception:
        logger.error("Failed to update draft status", exc_info=True)


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
def publish_draft(page: Page, draft: dict) -> dict[str, Any]:
    """Publish a draft via Playwright."""
    post_type    = draft["post_type"]
    body         = draft["body"]
    thread_tweets = draft["thread_tweets"]

    if post_type == "thread" and thread_tweets:
        # Validate lengths
        for i, t in enumerate(thread_tweets):
            if len(t) > 280:
                return {
                    "success": False,
                    "error": f"Tweet #{i+1} exceeds 280 chars ({len(t)}).",
                }
        return post_thread(page, thread_tweets)
    else:
        if len(body) > 280:
            return {
                "success": False,
                "error": f"Tweet exceeds 280 chars ({len(body)}).",
            }
        return post_tweet(page, body)


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
def main() -> None:
    for d in [DRAFTS_PATH, POSTED_PATH, SESSION_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 60)
    print("  AI Employee — Twitter/X Poster (Playwright)")
    print("=" * 60)
    print(f"  Drafts  : {DRAFTS_PATH}")
    print(f"  Posted  : {POSTED_PATH}")
    print(f"  Session : {SESSION_DIR}")
    print(f"  Poll    : every {POLL_INTERVAL}s")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    state = load_state()
    posted_hashes: set[str] = set(state.get("posted_hashes", []))

    with sync_playwright() as pw:
        # Persistent context — saves login session between runs
        context: BrowserContext = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )

        page: Page = context.pages[0] if context.pages else context.new_page()

        # Navigate to Twitter if not already there
        if "x.com" not in page.url and "twitter.com" not in page.url:
            page.goto(TWITTER_URL, timeout=60_000, wait_until="domcontentloaded")

        # Wait for login (QR-scan equivalent — manual login on first run)
        wait_for_login(page)

        logger.info("Twitter poster started. Polling every %ds...", POLL_INTERVAL)

        try:
            while True:
                drafts = sorted(DRAFTS_PATH.glob("*.md"))

                for draft_file in drafts:
                    draft = parse_draft(draft_file)
                    if draft is None:
                        continue

                    h = content_hash(draft["body"])
                    if h in posted_hashes:
                        logger.info("Skipping duplicate: %s", draft["filename"])
                        mark_draft_status(draft_file, "posted")
                        continue

                    logger.info("Publishing draft: %s", draft["filename"])
                    result = publish_draft(page, draft)

                    log_posted(draft, result["success"], result.get("error", ""))
                    mark_draft_status(
                        draft_file, "posted" if result["success"] else "failed"
                    )

                    if result["success"]:
                        posted_hashes.add(h)
                        state["post_count"] = state.get("post_count", 0) + 1

                    state["posted_hashes"] = list(posted_hashes)
                    state["last_run"]      = datetime.now(tz=PKT).isoformat()
                    save_state(state)

                    logger.info("Rate limit: waiting %ds...", RATE_LIMIT_SECONDS)
                    time.sleep(RATE_LIMIT_SECONDS)

                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print()
            logger.info("Shutting down Twitter poster...")

        state["posted_hashes"] = list(posted_hashes)
        state["last_run"]      = datetime.now(tz=PKT).isoformat()
        save_state(state)
        context.close()

    print("Twitter poster stopped. Goodbye!")


if __name__ == "__main__":
    main()
