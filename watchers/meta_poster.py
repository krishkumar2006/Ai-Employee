"""
Meta Poster — Free Edition (Playwright)
=============================================================
Posts content to Facebook Pages and Instagram via Playwright
browser automation. No API keys or tokens required.

Same pattern as whatsapp_watcher.py and twitter_poster.py:
  - First run: browser opens, you log in manually
  - Subsequent runs: session auto-restored

Supports:
  - Facebook: text posts, photo posts (local image file)
  - Instagram: photo posts, carousel posts (local image files)

Sessions saved at:
  vault/.meta_session/facebook/
  vault/.meta_session/instagram/

Draft frontmatter fields:
    platform:    facebook | instagram | both
    post_type:   text | photo | carousel
    image_path:  D:/path/to/image.jpg         (for photo posts)
    image_paths: D:/img1.jpg, D:/img2.jpg     (for carousel)
    status:      draft | ready | posted | failed
    schedule:    2026-03-01T10:00             (optional)

Prerequisites:
    pip install playwright python-dotenv
    playwright install chromium

No .env vars needed — just log in manually on first run.

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
PROJECT_ROOT  = Path(__file__).resolve().parent.parent
VAULT_PATH    = PROJECT_ROOT / "vault"
DRAFTS_PATH   = VAULT_PATH / "Meta_Drafts"
POSTED_PATH   = VAULT_PATH / "Meta_Posted"
SESSION_DIR   = VAULT_PATH / ".meta_session"
FB_SESSION    = SESSION_DIR / "facebook"
IG_SESSION    = SESSION_DIR / "instagram"

WATCHERS_DIR  = Path(__file__).resolve().parent
STATE_FILE    = WATCHERS_DIR / ".meta_state.json"

PKT = timezone(timedelta(hours=5))

POLL_INTERVAL      = 60
RATE_LIMIT_SECONDS = 30

FB_URL = "https://www.facebook.com"
IG_URL = "https://www.instagram.com"

# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")

# Facebook Page name/slug from .env (e.g. "MyBusiness" for fb.com/MyBusiness)
# Leave empty to post to personal profile/news feed instead
META_FB_PAGE_NAME = __import__("os").environ.get("META_FB_PAGE_NAME", "")

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
        logging.FileHandler(LOG_DIR / "meta_poster.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("meta_poster")


# ---------------------------------------------------------------------------
# Login Detection
# ---------------------------------------------------------------------------
def wait_for_fb_login(page: Page) -> None:
    """Wait until Facebook is fully loaded and user is logged in."""
    logger.info("Waiting for Facebook login...")
    logger.info("Log into facebook.com in the browser window. You have 10 minutes.")

    for _ in range(120):  # 10 minutes
        url = page.url
        if "facebook.com" in url and not any(
            x in url for x in ["login", "checkpoint", "recover"]
        ):
            try:
                el = page.query_selector(
                    '[aria-label="Facebook"], '
                    '[data-testid="royal_login_button"], '
                    'div[role="feed"], '
                    'div[aria-label="Home"]'
                )
                if el:
                    # Check it's the home feed not the login page button
                    btn = page.query_selector('[data-testid="royal_login_button"]')
                    if not btn:
                        logger.info("Facebook logged in!")
                        return
            except Exception:
                pass
        time.sleep(5)

    raise TimeoutError("Facebook login timeout — not logged in within 10 minutes.")


def wait_for_ig_login(page: Page) -> None:
    """Wait until Instagram is fully loaded and user is logged in."""
    logger.info("Waiting for Instagram login...")
    logger.info("Log into instagram.com in the browser window. You have 10 minutes.")

    for _ in range(120):
        url = page.url
        if "instagram.com" in url and "/accounts/login" not in url:
            try:
                el = page.query_selector(
                    'svg[aria-label="Home"], '
                    'a[href="/"]'
                )
                if el:
                    logger.info("Instagram logged in!")
                    return
            except Exception:
                pass
        time.sleep(5)

    raise TimeoutError("Instagram login timeout — not logged in within 10 minutes.")


# ---------------------------------------------------------------------------
# Facebook Posting
# ---------------------------------------------------------------------------
def fb_post_text(page: Page, message: str) -> dict[str, Any]:
    """Post a text message to Facebook Page or profile."""
    try:
        # Navigate to the Page (or home feed if no page configured)
        if META_FB_PAGE_NAME:
            page.goto(
                f"{FB_URL}/{META_FB_PAGE_NAME}",
                timeout=30_000, wait_until="domcontentloaded",
            )
        else:
            page.goto(FB_URL, timeout=30_000, wait_until="domcontentloaded")

        page.wait_for_timeout(3000)

        # Click on the compose / "What's on your mind?" box
        compose = page.query_selector(
            '[data-testid="status-attachment-mentions-input"], '
            '[aria-label="What\'s on your mind?"], '
            '[aria-label="Write something..."], '
            '[aria-placeholder="What\'s on your mind?"]'
        )
        if not compose:
            # Try clicking the visible placeholder text
            page.click(
                'div[role="button"]:has-text("What\'s on your mind"), '
                'div[role="button"]:has-text("Write something")',
                timeout=10_000,
            )
        else:
            compose.click()

        page.wait_for_timeout(1500)

        # Type in the dialog that appears
        dialog_input = page.wait_for_selector(
            'div[contenteditable="true"][role="textbox"], '
            'div[data-lexical-editor="true"]',
            timeout=10_000,
        )
        dialog_input.click()
        page.keyboard.type(message, delay=15)
        page.wait_for_timeout(1000)

        # Click Post button
        page.click(
            'div[aria-label="Post"] button[type="submit"], '
            'button[data-testid="react-composer-post-button"]',
            timeout=10_000,
        )
        page.wait_for_timeout(4000)

        logger.info("Facebook text post published.")
        return {"success": True}

    except Exception as e:
        logger.error("Facebook text post failed: %s", e)
        return {"success": False, "error": str(e)}


def fb_post_photo(page: Page, message: str, image_path: str) -> dict[str, Any]:
    """Post a photo to Facebook Page or profile."""
    try:
        img = Path(image_path)
        if not img.exists():
            return {"success": False, "error": f"Image not found: {image_path}"}

        if META_FB_PAGE_NAME:
            page.goto(
                f"{FB_URL}/{META_FB_PAGE_NAME}",
                timeout=30_000, wait_until="domcontentloaded",
            )
        else:
            page.goto(FB_URL, timeout=30_000, wait_until="domcontentloaded")

        page.wait_for_timeout(3000)

        # Click compose area
        page.click(
            'div[role="button"]:has-text("What\'s on your mind"), '
            'div[role="button"]:has-text("Write something")',
            timeout=10_000,
        )
        page.wait_for_timeout(1500)

        # Click "Photo/video" button inside the composer
        page.click(
            '[data-testid="photo-attachments-composer-icon"], '
            'div[aria-label="Photo/video"], '
            'span:has-text("Photo/video")',
            timeout=8_000,
        )
        page.wait_for_timeout(1000)

        # Handle file chooser
        with page.expect_file_chooser(timeout=10_000) as fc_info:
            page.click(
                'input[type="file"], '
                'button:has-text("Add photos/videos"), '
                'div[aria-label="Add Photos/Videos"]',
                timeout=8_000,
            )
        fc_info.value.set_files(str(img))
        page.wait_for_timeout(3000)

        # Add caption
        caption_input = page.query_selector(
            'div[contenteditable="true"][role="textbox"], '
            'div[data-lexical-editor="true"]'
        )
        if caption_input and message:
            caption_input.click()
            page.keyboard.type(message, delay=15)
            page.wait_for_timeout(500)

        # Post
        page.click(
            'div[aria-label="Post"] button[type="submit"], '
            'button[data-testid="react-composer-post-button"]',
            timeout=10_000,
        )
        page.wait_for_timeout(4000)

        logger.info("Facebook photo post published.")
        return {"success": True}

    except Exception as e:
        logger.error("Facebook photo post failed: %s", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Instagram Posting
# ---------------------------------------------------------------------------
def ig_post_photo(page: Page, image_path: str, caption: str) -> dict[str, Any]:
    """Post a single photo to Instagram."""
    try:
        img = Path(image_path)
        if not img.exists():
            return {"success": False, "error": f"Image not found: {image_path}"}

        page.goto(IG_URL, timeout=30_000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Click the + (create new post) button
        page.click(
            'svg[aria-label="New post"], '
            'a[aria-label="New post"], '
            'span:has-text("Create")',
            timeout=10_000,
        )
        page.wait_for_timeout(1500)

        # Handle file chooser triggered by clicking "Select from computer"
        with page.expect_file_chooser(timeout=10_000) as fc_info:
            page.click(
                'button:has-text("Select from computer"), '
                'input[type="file"]',
                timeout=8_000,
            )
        fc_info.value.set_files(str(img))
        page.wait_for_timeout(3000)

        # If crop screen appears, click Next
        try:
            page.click('button:has-text("Next")', timeout=5_000)
            page.wait_for_timeout(1500)
        except Exception:
            pass

        # Filters screen — click Next
        try:
            page.click('button:has-text("Next")', timeout=5_000)
            page.wait_for_timeout(1500)
        except Exception:
            pass

        # Caption screen — add caption
        if caption:
            try:
                cap_area = page.wait_for_selector(
                    'textarea[aria-label="Write a caption..."], '
                    'div[aria-label="Write a caption..."][contenteditable]',
                    timeout=8_000,
                )
                cap_area.click()
                page.keyboard.type(caption, delay=15)
                page.wait_for_timeout(500)
            except Exception:
                logger.warning("Could not find caption area — posting without caption.")

        # Share
        page.click('button:has-text("Share")', timeout=10_000)
        page.wait_for_timeout(5000)

        logger.info("Instagram photo posted.")
        return {"success": True}

    except Exception as e:
        logger.error("Instagram photo post failed: %s", e)
        return {"success": False, "error": str(e)}


def ig_post_carousel(page: Page, image_paths: list[str], caption: str) -> dict[str, Any]:
    """Post a carousel (multiple photos) to Instagram."""
    try:
        valid_paths = []
        for p in image_paths:
            img = Path(p)
            if img.exists():
                valid_paths.append(str(img))
            else:
                logger.warning("Carousel image not found, skipping: %s", p)

        if len(valid_paths) < 2:
            return {"success": False, "error": "Carousel needs at least 2 valid images."}

        page.goto(IG_URL, timeout=30_000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Click + (new post)
        page.click(
            'svg[aria-label="New post"], '
            'a[aria-label="New post"], '
            'span:has-text("Create")',
            timeout=10_000,
        )
        page.wait_for_timeout(1500)

        # Upload multiple files
        with page.expect_file_chooser(timeout=10_000) as fc_info:
            page.click(
                'button:has-text("Select from computer"), '
                'input[type="file"]',
                timeout=8_000,
            )
        fc_info.value.set_files(valid_paths)
        page.wait_for_timeout(3000)

        # Next through crop/filter screens
        for _ in range(2):
            try:
                page.click('button:has-text("Next")', timeout=5_000)
                page.wait_for_timeout(1500)
            except Exception:
                pass

        # Caption
        if caption:
            try:
                cap_area = page.wait_for_selector(
                    'textarea[aria-label="Write a caption..."], '
                    'div[aria-label="Write a caption..."][contenteditable]',
                    timeout=8_000,
                )
                cap_area.click()
                page.keyboard.type(caption, delay=15)
                page.wait_for_timeout(500)
            except Exception:
                logger.warning("Could not find caption area.")

        # Share
        page.click('button:has-text("Share")', timeout=10_000)
        page.wait_for_timeout(5000)

        logger.info("Instagram carousel posted (%d images).", len(valid_paths))
        return {"success": True}

    except Exception as e:
        logger.error("Instagram carousel failed: %s", e)
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# State + Draft Parsing
# ---------------------------------------------------------------------------
def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
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


def parse_draft(file_path: Path) -> Optional[dict[str, Any]]:
    """Parse a Meta draft .md file."""
    try:
        text = file_path.read_text(encoding="utf-8").strip()
    except Exception:
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
        return None
    if metadata.get("status", "draft") != "ready":
        return None

    # Schedule check
    schedule_str = metadata.get("schedule", "")
    if schedule_str:
        try:
            dt = datetime.fromisoformat(schedule_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=PKT)
            if dt > datetime.now(tz=PKT):
                return None
        except ValueError:
            pass

    # Parse image paths (comma-separated for carousel)
    image_paths_raw = metadata.get("image_paths", "")
    image_paths = [p.strip() for p in image_paths_raw.split(",") if p.strip()]

    return {
        "file":         str(file_path),
        "filename":     file_path.name,
        "body":         body,
        "platform":     metadata.get("platform", "facebook"),
        "post_type":    metadata.get("post_type", "text"),
        "image_path":   metadata.get("image_path", ""),
        "image_paths":  image_paths,
        "metadata":     metadata,
    }


def log_posted(
    draft: dict, platform: str, success: bool, error: str = ""
) -> None:
    POSTED_PATH.mkdir(parents=True, exist_ok=True)
    now    = datetime.now(tz=PKT)
    ts     = now.strftime("%Y-%m-%dT%H-%M-%S")
    status = "posted" if success else "failed"

    content = (
        f"---\ntype: meta_{platform}_post\nstatus: {status}\n"
        f"source_draft: {draft['filename']}\nposted_at: {now.isoformat()}\n"
        f"platform: {platform}\npost_type: {draft['post_type']}\n"
    )
    if error:
        content += f"error: {error}\n"
    content += f"---\n\n## {'Posted' if success else 'Failed'}\n\n{draft['body'][:500]}\n"

    filename = f"META_{platform.upper()}_{status}_{ts}.md"
    (POSTED_PATH / filename).write_text(content, encoding="utf-8")
    logger.info("Post log saved: %s", filename)


def mark_draft_status(draft_path: Path, status: str) -> None:
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
def publish_draft(
    fb_page: Optional[Page],
    ig_page: Optional[Page],
    draft: dict,
) -> dict[str, Any]:
    """Publish draft to target platform(s). Returns per-platform results."""
    platform  = draft["platform"]
    post_type = draft["post_type"]
    body      = draft["body"]
    img       = draft["image_path"]
    imgs      = draft["image_paths"]

    results: dict[str, Any] = {}

    # --- Facebook ---
    if platform in ("facebook", "both") and fb_page:
        if post_type == "photo" and img:
            results["facebook"] = fb_post_photo(fb_page, body, img)
        else:
            results["facebook"] = fb_post_text(fb_page, body)

    # --- Instagram ---
    if platform in ("instagram", "both") and ig_page:
        if post_type == "carousel" and len(imgs) >= 2:
            results["instagram"] = ig_post_carousel(ig_page, imgs, body)
        elif img:
            results["instagram"] = ig_post_photo(ig_page, img, body)
        else:
            results["instagram"] = {
                "success": False,
                "error": "Instagram requires image_path — text-only not supported.",
            }

    if not results:
        return {
            "facebook": {"success": False, "error": "No browser available for platform."}
        }

    return results


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
def main() -> None:
    for d in [DRAFTS_PATH, POSTED_PATH, FB_SESSION, IG_SESSION]:
        d.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 60)
    print("  AI Employee — Meta Poster (Facebook + Instagram)")
    print("  Free Edition — Playwright browser automation")
    print("=" * 60)
    print(f"  Drafts    : {DRAFTS_PATH}")
    print(f"  FB Session: {FB_SESSION}")
    print(f"  IG Session: {IG_SESSION}")
    print(f"  Poll      : every {POLL_INTERVAL}s")
    print("  Press Ctrl+C to stop")
    print("=" * 60)
    print()

    state = load_state()
    posted_hashes: set[str] = set(state.get("posted_hashes", []))

    with sync_playwright() as pw:
        # --- Facebook context ---
        fb_context: BrowserContext = pw.chromium.launch_persistent_context(
            user_data_dir=str(FB_SESSION),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        fb_page: Page = fb_context.pages[0] if fb_context.pages else fb_context.new_page()
        if "facebook.com" not in fb_page.url:
            fb_page.goto(FB_URL, timeout=60_000, wait_until="domcontentloaded")
        wait_for_fb_login(fb_page)

        # --- Instagram context ---
        ig_context: BrowserContext = pw.chromium.launch_persistent_context(
            user_data_dir=str(IG_SESSION),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        ig_page: Page = ig_context.pages[0] if ig_context.pages else ig_context.new_page()
        if "instagram.com" not in ig_page.url:
            ig_page.goto(IG_URL, timeout=60_000, wait_until="domcontentloaded")
        wait_for_ig_login(ig_page)

        logger.info("Both platforms ready. Polling every %ds...", POLL_INTERVAL)

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

                    logger.info("Publishing: %s", draft["filename"])
                    results = publish_draft(fb_page, ig_page, draft)

                    any_success = any(r.get("success") for r in results.values())
                    all_success = all(r.get("success") for r in results.values())

                    for plat, res in results.items():
                        log_posted(draft, plat, res["success"], res.get("error", ""))

                    if all_success:
                        mark_draft_status(draft_file, "posted")
                    elif any_success:
                        mark_draft_status(draft_file, "partial")
                    else:
                        mark_draft_status(draft_file, "failed")

                    if any_success:
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
            logger.info("Shutting down Meta poster...")

        state["posted_hashes"] = list(posted_hashes)
        save_state(state)
        fb_context.close()
        ig_context.close()

    print("Meta poster stopped. Goodbye!")


if __name__ == "__main__":
    main()
