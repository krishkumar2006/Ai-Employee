"""
Meta MCP Server — Free Edition (Playwright)
====================================================
A Model Context Protocol server that gives Claude direct tool access
to Facebook and Instagram via Playwright browser automation.

No API keys or tokens required — uses saved browser sessions from
meta_poster.py (run it first and log in manually).

Sessions must exist at:
  vault/.meta_session/facebook/
  vault/.meta_session/instagram/

Tools exposed:
  WRITE (requires explicit user confirmation):
    1. meta_post_facebook    — Post text to Facebook (Page or profile)
    2. meta_post_facebook_photo — Post photo to Facebook
    3. meta_post_instagram   — Post photo to Instagram
    4. meta_post_ig_carousel — Post carousel to Instagram

Part of the Personal AI Employee system.
"""

import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FB_SESSION = PROJECT_ROOT / "vault" / ".meta_session" / "facebook"
IG_SESSION = PROJECT_ROOT / "vault" / ".meta_session" / "instagram"

PKT = timezone(timedelta(hours=5))

META_FB_PAGE_NAME = __import__("os").environ.get("META_FB_PAGE_NAME", "")

# ---------------------------------------------------------------------------
# Logging (stderr — keeps MCP stdio clean)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("meta_mcp")


# ---------------------------------------------------------------------------
# Playwright Helpers
# ---------------------------------------------------------------------------
def _get_fb_context():
    from playwright.sync_api import sync_playwright
    if not FB_SESSION.exists():
        raise RuntimeError(
            "No saved Facebook session. Run meta_poster.py first and log in."
        )
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(FB_SESSION),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, ctx


def _get_ig_context():
    from playwright.sync_api import sync_playwright
    if not IG_SESSION.exists():
        raise RuntimeError(
            "No saved Instagram session. Run meta_poster.py first and log in."
        )
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(IG_SESSION),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, ctx


def _now_pkt() -> str:
    return datetime.now(tz=PKT).strftime("%Y-%m-%d %H:%M:%S PKT")


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "ai-employee-meta",
    instructions=(
        "Meta (Facebook + Instagram) integration via Playwright (free, no API keys). "
        "Requires saved sessions from meta_poster.py first run. "
        "All tools post live content — always confirm with user first."
    ),
)


# ===========================================================================
# WRITE TOOLS
# ===========================================================================

@mcp.tool()
def meta_post_facebook(message: str) -> str:
    """Post a text message to Facebook Page or profile.
    LIVE ACTION — only call after user has explicitly confirmed.

    Args:
        message: The post text.
    """
    if not message.strip():
        return "ERROR: message cannot be empty."

    pw = None
    try:
        pw, ctx = _get_fb_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if META_FB_PAGE_NAME:
            page.goto(
                f"https://www.facebook.com/{META_FB_PAGE_NAME}",
                timeout=30_000, wait_until="domcontentloaded",
            )
        else:
            page.goto("https://www.facebook.com", timeout=30_000, wait_until="domcontentloaded")

        page.wait_for_timeout(3000)

        page.click(
            'div[role="button"]:has-text("What\'s on your mind"), '
            'div[role="button"]:has-text("Write something")',
            timeout=10_000,
        )
        page.wait_for_timeout(1500)

        dialog = page.wait_for_selector(
            'div[contenteditable="true"][role="textbox"], '
            'div[data-lexical-editor="true"]',
            timeout=10_000,
        )
        dialog.click()
        page.keyboard.type(message, delay=15)
        page.wait_for_timeout(1000)

        page.click(
            'div[aria-label="Post"] button[type="submit"], '
            'button[data-testid="react-composer-post-button"]',
            timeout=10_000,
        )
        page.wait_for_timeout(4000)

        ctx.close()
        pw.stop()
        logger.info("Facebook text post published via MCP.")

        return (
            f"FACEBOOK POST PUBLISHED\n"
            f"  Posted: {_now_pkt()}\n"
            f"  Chars:  {len(message)}\n"
            f"  Text:   {message[:100]}{'...' if len(message) > 100 else ''}"
        )

    except Exception as e:
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        return f"ERROR: {e}"


@mcp.tool()
def meta_post_facebook_photo(message: str, image_path: str) -> str:
    """Post a photo to Facebook Page or profile.
    LIVE ACTION — only call after user has explicitly confirmed.

    Args:
        message: Caption / post text.
        image_path: Absolute local path to the image file.
    """
    img = Path(image_path)
    if not img.exists():
        return f"ERROR: Image not found: {image_path}"

    pw = None
    try:
        pw, ctx = _get_fb_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if META_FB_PAGE_NAME:
            page.goto(
                f"https://www.facebook.com/{META_FB_PAGE_NAME}",
                timeout=30_000, wait_until="domcontentloaded",
            )
        else:
            page.goto("https://www.facebook.com", timeout=30_000, wait_until="domcontentloaded")

        page.wait_for_timeout(3000)

        page.click(
            'div[role="button"]:has-text("What\'s on your mind"), '
            'div[role="button"]:has-text("Write something")',
            timeout=10_000,
        )
        page.wait_for_timeout(1500)

        page.click(
            '[data-testid="photo-attachments-composer-icon"], '
            'div[aria-label="Photo/video"]',
            timeout=8_000,
        )
        page.wait_for_timeout(1000)

        with page.expect_file_chooser(timeout=10_000) as fc_info:
            page.click('input[type="file"]', timeout=8_000)
        fc_info.value.set_files(str(img))
        page.wait_for_timeout(3000)

        if message:
            cap = page.query_selector(
                'div[contenteditable="true"][role="textbox"], '
                'div[data-lexical-editor="true"]'
            )
            if cap:
                cap.click()
                page.keyboard.type(message, delay=15)
                page.wait_for_timeout(500)

        page.click(
            'div[aria-label="Post"] button[type="submit"], '
            'button[data-testid="react-composer-post-button"]',
            timeout=10_000,
        )
        page.wait_for_timeout(4000)

        ctx.close()
        pw.stop()
        logger.info("Facebook photo post published via MCP.")

        return (
            f"FACEBOOK PHOTO PUBLISHED\n"
            f"  Posted: {_now_pkt()}\n"
            f"  Image:  {image_path}"
        )

    except Exception as e:
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        return f"ERROR: {e}"


@mcp.tool()
def meta_post_instagram(image_path: str, caption: str = "") -> str:
    """Post a photo to Instagram.
    LIVE ACTION — only call after user has explicitly confirmed.

    Args:
        image_path: Absolute local path to the image file (JPG/PNG).
        caption: Post caption (optional).
    """
    img = Path(image_path)
    if not img.exists():
        return f"ERROR: Image not found: {image_path}"

    pw = None
    try:
        pw, ctx = _get_ig_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com", timeout=30_000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        page.click(
            'svg[aria-label="New post"], '
            'a[aria-label="New post"], '
            'span:has-text("Create")',
            timeout=10_000,
        )
        page.wait_for_timeout(1500)

        with page.expect_file_chooser(timeout=10_000) as fc_info:
            page.click(
                'button:has-text("Select from computer"), '
                'input[type="file"]',
                timeout=8_000,
            )
        fc_info.value.set_files(str(img))
        page.wait_for_timeout(3000)

        for _ in range(2):
            try:
                page.click('button:has-text("Next")', timeout=5_000)
                page.wait_for_timeout(1500)
            except Exception:
                pass

        if caption:
            try:
                cap = page.wait_for_selector(
                    'textarea[aria-label="Write a caption..."], '
                    'div[aria-label="Write a caption..."][contenteditable]',
                    timeout=8_000,
                )
                cap.click()
                page.keyboard.type(caption, delay=15)
                page.wait_for_timeout(500)
            except Exception:
                pass

        page.click('button:has-text("Share")', timeout=10_000)
        page.wait_for_timeout(5000)

        ctx.close()
        pw.stop()
        logger.info("Instagram photo published via MCP.")

        return (
            f"INSTAGRAM PHOTO PUBLISHED\n"
            f"  Posted:  {_now_pkt()}\n"
            f"  Image:   {image_path}\n"
            f"  Caption: {caption[:100]}{'...' if len(caption) > 100 else ''}"
        )

    except Exception as e:
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        return f"ERROR: {e}"


@mcp.tool()
def meta_post_ig_carousel(image_paths: list[str], caption: str = "") -> str:
    """Post a carousel (2–10 images) to Instagram.
    LIVE ACTION — only call after user has explicitly confirmed.

    Args:
        image_paths: List of absolute local paths to image files.
        caption: Carousel caption (optional).
    """
    if len(image_paths) < 2:
        return "ERROR: Carousel requires at least 2 images."
    if len(image_paths) > 10:
        return "ERROR: Maximum 10 images per carousel."

    valid = [str(Path(p)) for p in image_paths if Path(p).exists()]
    if len(valid) < 2:
        return "ERROR: Need at least 2 valid (existing) image files."

    pw = None
    try:
        pw, ctx = _get_ig_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com", timeout=30_000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        page.click(
            'svg[aria-label="New post"], '
            'a[aria-label="New post"], '
            'span:has-text("Create")',
            timeout=10_000,
        )
        page.wait_for_timeout(1500)

        with page.expect_file_chooser(timeout=10_000) as fc_info:
            page.click(
                'button:has-text("Select from computer"), '
                'input[type="file"]',
                timeout=8_000,
            )
        fc_info.value.set_files(valid)
        page.wait_for_timeout(3000)

        for _ in range(2):
            try:
                page.click('button:has-text("Next")', timeout=5_000)
                page.wait_for_timeout(1500)
            except Exception:
                pass

        if caption:
            try:
                cap = page.wait_for_selector(
                    'textarea[aria-label="Write a caption..."], '
                    'div[aria-label="Write a caption..."][contenteditable]',
                    timeout=8_000,
                )
                cap.click()
                page.keyboard.type(caption, delay=15)
                page.wait_for_timeout(500)
            except Exception:
                pass

        page.click('button:has-text("Share")', timeout=10_000)
        page.wait_for_timeout(5000)

        ctx.close()
        pw.stop()
        logger.info("Instagram carousel published via MCP (%d images).", len(valid))

        return (
            f"INSTAGRAM CAROUSEL PUBLISHED\n"
            f"  Posted: {_now_pkt()}\n"
            f"  Images: {len(valid)}\n"
            f"  Caption: {caption[:100]}{'...' if len(caption) > 100 else ''}"
        )

    except Exception as e:
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not FB_SESSION.exists() or not IG_SESSION.exists():
        logger.warning(
            "Session directories missing — run meta_poster.py first and log in."
        )
    logger.info("Starting Meta MCP server (Playwright)...")
    mcp.run(transport="stdio")
