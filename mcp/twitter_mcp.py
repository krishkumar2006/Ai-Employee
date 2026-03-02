"""
Twitter/X MCP Server — Free Edition (Playwright)
====================================================
A Model Context Protocol server that gives Claude direct tool access
to X (Twitter) via Playwright browser automation.

No API keys required — uses the saved browser session from twitter_poster.py.
Session must exist in vault/.twitter_session/ (created by running twitter_poster.py
for the first time and logging in manually).

Tools exposed:
  READ  (immediate):
    1. twitter_get_profile   — Authenticated user's profile info

  WRITE (requires explicit user confirmation):
    2. twitter_post_tweet    — Post a plain text tweet
    3. twitter_post_thread   — Post a thread (list of tweets in sequence)

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

SESSION_DIR = PROJECT_ROOT / "vault" / ".twitter_session"
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Logging (stderr — keeps MCP stdio clean)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("twitter_mcp")


# ---------------------------------------------------------------------------
# Playwright Helpers
# ---------------------------------------------------------------------------
def _get_context():
    """Launch a persistent Playwright browser context using saved session."""
    from playwright.sync_api import sync_playwright

    if not SESSION_DIR.exists():
        raise RuntimeError(
            "No saved Twitter session found. "
            "Run twitter_poster.py first and log in manually."
        )

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(SESSION_DIR),
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, context


def _now_pkt() -> str:
    return datetime.now(tz=PKT).strftime("%Y-%m-%d %H:%M:%S PKT")


def _wait_for_login(page) -> bool:
    """Return True if already logged in."""
    try:
        page.wait_for_selector(
            'a[data-testid="AppTabBar_Home_Link"], '
            'a[data-testid="SideNav_NewTweet_Button"]',
            timeout=15_000,
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "ai-employee-twitter",
    instructions=(
        "X (Twitter) integration for the AI Employee (free, Playwright-based). "
        "Requires saved session from twitter_poster.py first run. "
        "READ tools execute immediately. "
        "WRITE tools post live to Twitter — always confirm with user first."
    ),
)


# ===========================================================================
# READ TOOLS
# ===========================================================================

@mcp.tool()
def twitter_get_profile() -> str:
    """Get the authenticated X (Twitter) account's profile info.
    Read-only — executes immediately using saved browser session.
    """
    pw = None
    try:
        pw, context = _get_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://x.com/home", timeout=30_000)
        page.wait_for_timeout(3000)

        if not _wait_for_login(page):
            context.close()
            pw.stop()
            return (
                "ERROR: Not logged in. Run twitter_poster.py first "
                "and log in manually to create a session."
            )

        profile: dict = {}

        # Get username from profile link
        try:
            nav = page.query_selector('a[data-testid="AppTabBar_Profile_Link"]')
            if nav:
                href = nav.get_attribute("href") or ""
                if href.startswith("/"):
                    profile["username"] = href.strip("/")
        except Exception:
            pass

        # Navigate to profile page
        if profile.get("username"):
            page.goto(f"https://x.com/{profile['username']}", timeout=30_000)
            page.wait_for_timeout(2500)

            for selector, key in [
                ('a[href$="/followers"] span span', "followers"),
                ('a[href$="/following"] span span', "following"),
            ]:
                try:
                    el = page.query_selector(selector)
                    if el:
                        profile[key] = el.inner_text().strip()
                except Exception:
                    pass

            try:
                name_el = page.query_selector('div[data-testid="UserName"] span')
                if name_el:
                    profile["name"] = name_el.inner_text().strip()
            except Exception:
                pass

            try:
                bio_el = page.query_selector('div[data-testid="UserDescription"]')
                if bio_el:
                    profile["bio"] = bio_el.inner_text().strip()[:200]
            except Exception:
                pass

        context.close()
        pw.stop()

        lines = [
            "X (TWITTER) PROFILE",
            f"  Handle:    @{profile.get('username', 'N/A')}",
            f"  Name:      {profile.get('name', 'N/A')}",
            f"  Followers: {profile.get('followers', 'N/A')}",
            f"  Following: {profile.get('following', 'N/A')}",
            f"  Bio:       {profile.get('bio', '(none)')}",
            f"  As of:     {_now_pkt()}",
            "",
            "  Note: Detailed metrics require paid Twitter API.",
        ]
        return "\n".join(lines)

    except Exception as e:
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        return f"ERROR: {e}"


# ===========================================================================
# WRITE TOOLS (live — always confirm with user first)
# ===========================================================================

@mcp.tool()
def twitter_post_tweet(text: str) -> str:
    """Post a tweet to X (Twitter).
    LIVE ACTION — only call this after the user has explicitly confirmed.

    Args:
        text: Tweet text (max 280 characters).
    """
    if len(text) > 280:
        return (
            f"ERROR: Tweet is {len(text)} chars — exceeds 280 limit.\n"
            "Shorten it or use twitter_post_thread."
        )
    if not text.strip():
        return "ERROR: Tweet text cannot be empty."

    pw = None
    try:
        pw, context = _get_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://x.com/home", timeout=30_000)
        page.wait_for_timeout(2000)

        if not _wait_for_login(page):
            context.close()
            pw.stop()
            return "ERROR: Not logged in. Run twitter_poster.py first."

        # Click compose button
        page.click('a[data-testid="SideNav_NewTweet_Button"]')
        page.wait_for_timeout(1500)

        # Type tweet
        textarea = page.wait_for_selector(
            'div[data-testid="tweetTextarea_0"]', timeout=10_000
        )
        textarea.click()
        page.wait_for_timeout(300)
        page.keyboard.type(text, delay=20)
        page.wait_for_timeout(1000)

        # Post
        page.click('button[data-testid="tweetButtonInline"]')
        page.wait_for_timeout(3000)

        context.close()
        pw.stop()

        logger.info("Tweet posted via MCP.")
        return (
            f"TWEET POSTED\n"
            f"  Chars:  {len(text)}\n"
            f"  Posted: {_now_pkt()}\n"
            f"  Text:   {text[:100]}{'...' if len(text) > 100 else ''}"
        )

    except Exception as e:
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        return f"ERROR: {e}"


@mcp.tool()
def twitter_post_thread(tweets: list[str]) -> str:
    """Post a thread — each string in the list becomes one reply tweet.
    LIVE ACTION — only call this after the user has explicitly confirmed.

    Args:
        tweets: List of tweet texts. Each must be ≤280 chars.
                Min 2 tweets, max 25 per thread.
    """
    if len(tweets) < 2:
        return "ERROR: A thread requires at least 2 tweets."
    if len(tweets) > 25:
        return "ERROR: Maximum 25 tweets per thread."

    for i, t in enumerate(tweets, 1):
        if len(t) > 280:
            return f"ERROR: Tweet #{i} is {len(t)} chars — exceeds 280 limit."
        if not t.strip():
            return f"ERROR: Tweet #{i} is empty."

    pw = None
    try:
        pw, context = _get_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://x.com/home", timeout=30_000)
        page.wait_for_timeout(2000)

        if not _wait_for_login(page):
            context.close()
            pw.stop()
            return "ERROR: Not logged in. Run twitter_poster.py first."

        # Open compose
        page.click('a[data-testid="SideNav_NewTweet_Button"]')
        page.wait_for_timeout(1500)

        for i, text in enumerate(tweets):
            if i == 0:
                textarea = page.wait_for_selector(
                    'div[data-testid="tweetTextarea_0"]', timeout=10_000
                )
            else:
                page.click('button[data-testid="addButton"]')
                page.wait_for_timeout(1000)
                textareas = page.query_selector_all('div[data-testid^="tweetTextarea_"]')
                textarea = textareas[-1] if textareas else None

            if textarea:
                textarea.click()
                page.wait_for_timeout(300)
                page.keyboard.type(text, delay=20)
                page.wait_for_timeout(800)

        # Post all
        page.click('button[data-testid="tweetButtonInline"]')
        page.wait_for_timeout(3000)

        context.close()
        pw.stop()

        logger.info("Thread posted via MCP (%d tweets).", len(tweets))
        lines = [f"THREAD POSTED ({len(tweets)} tweets)", f"  Posted: {_now_pkt()}", ""]
        for i, t in enumerate(tweets, 1):
            preview = t[:80] + "..." if len(t) > 80 else t
            lines.append(f"  #{i}: {preview}")
        return "\n".join(lines)

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
    if not SESSION_DIR.exists():
        logger.warning(
            "No saved session found at %s — "
            "run twitter_poster.py first and log in manually.",
            SESSION_DIR,
        )
    logger.info("Starting Twitter/X MCP server (Playwright)...")
    mcp.run(transport="stdio")
