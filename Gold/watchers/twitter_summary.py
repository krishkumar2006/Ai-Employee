"""
Twitter/X Summary — Free Edition (Playwright)
=============================================================
Reads recent posts from vault/Twitter_Posted/ logs and generates
a markdown summary. Uses Playwright to scrape basic profile info
from twitter.com if a saved session exists.

Can be run:
  - On-demand:  python twitter_summary.py
  - Scheduled:  called by orchestrator
  - With Claude: python twitter_summary.py --claude

No API keys required — uses saved browser session from twitter_poster.py.

Part of the Personal AI Employee system.
"""

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

VAULT_PATH  = PROJECT_ROOT / "vault"
PLANS_PATH  = VAULT_PATH / "Plans"
POSTED_PATH = VAULT_PATH / "Twitter_Posted"
SESSION_DIR = VAULT_PATH / ".twitter_session"

PKT = timezone(timedelta(hours=5))

load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("twitter_summary")


# ---------------------------------------------------------------------------
# Local Vault Log Reader
# ---------------------------------------------------------------------------
def read_local_posted() -> list[dict[str, Any]]:
    """Read recently posted tweets from vault/Twitter_Posted/ log files."""
    if not POSTED_PATH.exists():
        return []

    posts = []
    for f in sorted(POSTED_PATH.glob("TWITTER_posted_*.md"), reverse=True)[:30]:
        try:
            text = f.read_text(encoding="utf-8")
            metadata: dict[str, str] = {}
            body = ""
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            metadata[k.strip()] = v.strip()
                    body = parts[2].strip()
                    # Remove "## Posted" header
                    if body.startswith("##"):
                        body = "\n".join(body.splitlines()[1:]).strip()
            posts.append({
                "filename":  f.name,
                "posted_at": metadata.get("posted_at", ""),
                "post_type": metadata.get("post_type", "tweet"),
                "char_count": metadata.get("char_count", ""),
                "body":      body[:200],
            })
        except Exception:
            pass
    return posts


# ---------------------------------------------------------------------------
# Profile Scraping via Playwright (optional — needs saved session)
# ---------------------------------------------------------------------------
def scrape_profile() -> dict[str, Any]:
    """Scrape basic profile info from twitter.com using saved session."""
    if not SESSION_DIR.exists():
        logger.info("No saved Twitter session found — skipping profile scrape.")
        return {}

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(SESSION_DIR),
                headless=True,  # Headless for summary — no UI needed
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://x.com/home", timeout=30_000)
            page.wait_for_timeout(4000)

            profile: dict[str, Any] = {}

            # Try to get username from nav
            try:
                # Profile link in sidebar
                nav_link = page.query_selector('a[data-testid="AppTabBar_Profile_Link"]')
                if nav_link:
                    href = nav_link.get_attribute("href") or ""
                    if href.startswith("/"):
                        profile["username"] = href.strip("/")
            except Exception:
                pass

            # Navigate to own profile for follower counts
            if profile.get("username"):
                page.goto(f"https://x.com/{profile['username']}", timeout=30_000)
                page.wait_for_timeout(3000)

                try:
                    # Follower count
                    followers_el = page.query_selector(
                        'a[href$="/followers"] span span, '
                        'a[href*="/verified_followers"] span span'
                    )
                    if followers_el:
                        profile["followers"] = followers_el.inner_text().strip()
                except Exception:
                    pass

                try:
                    # Following count
                    following_el = page.query_selector(
                        'a[href$="/following"] span span'
                    )
                    if following_el:
                        profile["following"] = following_el.inner_text().strip()
                except Exception:
                    pass

                try:
                    # Display name
                    name_el = page.query_selector('div[data-testid="UserName"] span')
                    if name_el:
                        profile["name"] = name_el.inner_text().strip()
                except Exception:
                    pass

                try:
                    # Bio
                    bio_el = page.query_selector('div[data-testid="UserDescription"]')
                    if bio_el:
                        profile["bio"] = bio_el.inner_text().strip()[:200]
                except Exception:
                    pass

            context.close()
            return profile

    except Exception as e:
        logger.warning("Profile scrape failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Summary Generation
# ---------------------------------------------------------------------------
def generate_summary(
    profile: dict[str, Any],
    local_posts: list[dict[str, Any]],
) -> str:
    """Build a markdown summary of Twitter activity."""
    now = datetime.now(tz=PKT)
    lines: list[str] = []

    lines.append("# X (Twitter) Summary")
    lines.append("")
    lines.append(f"> Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} PKT")
    lines.append(f"> Method: Playwright browser (free, no API key)")
    lines.append("")

    # ----- Profile -----
    if profile:
        lines.append("## Account Overview")
        lines.append("")
        if profile.get("username"):
            lines.append(f"- **Handle:** @{profile['username']}")
        if profile.get("name"):
            lines.append(f"- **Name:** {profile['name']}")
        if profile.get("followers"):
            lines.append(f"- **Followers:** {profile['followers']}")
        if profile.get("following"):
            lines.append(f"- **Following:** {profile['following']}")
        if profile.get("bio"):
            lines.append(f"- **Bio:** {profile['bio']}")
        lines.append("")
        lines.append(
            "> ℹ️ Detailed metrics (likes, impressions) require paid API — not shown."
        )
        lines.append("")
    else:
        lines.append("## Account Overview")
        lines.append("")
        lines.append(
            "*Run `twitter_poster.py` first to create a saved session, "
            "then profile info will appear here.*"
        )
        lines.append("")

    # ----- Recent Posts (Local) -----
    if local_posts:
        lines.append(f"## Recently Posted ({len(local_posts)} entries)")
        lines.append("")

        # Count by type
        tweets_count  = sum(1 for p in local_posts if p["post_type"] == "tweet")
        threads_count = sum(1 for p in local_posts if p["post_type"] == "thread")

        lines.append(f"- Tweets: **{tweets_count}**")
        lines.append(f"- Threads: **{threads_count}**")
        lines.append("")

        lines.append("### Post Log (recent 10)")
        lines.append("")

        for post in local_posts[:10]:
            ts = post["posted_at"][:19] if len(post["posted_at"]) >= 19 else post["posted_at"]
            chars = post["char_count"]
            ptype = post["post_type"]
            body  = post["body"]

            lines.append(f"**{ts}** | `{ptype}` | {chars} chars")
            if body:
                preview = body[:100] + "..." if len(body) > 100 else body
                lines.append(f"> {preview}")
            lines.append("")

    else:
        lines.append("## Activity")
        lines.append("")
        lines.append("*No posted tweets found in vault/Twitter_Posted/ yet.*")
        lines.append("")

    return "\n".join(lines)


def enhance_with_claude(summary: str) -> str:
    """Send raw summary to Claude for AI-enhanced insights."""
    prompt = (
        "You are the AI Employee social media analyst.\n"
        "Below is a Twitter/X activity summary.\n"
        "Add a section called '## AI Insights' with:\n"
        "- Best posting times based on the log\n"
        "- Content type recommendations (tweets vs threads)\n"
        "- 3 actionable tips to grow engagement\n"
        "Keep it concise.\n\n"
        f"{summary}"
    )
    try:
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
            return result.stdout.strip()
        logger.warning("Claude enhancement failed, returning raw summary.")
        return summary
    except Exception:
        logger.warning("Claude CLI not available, returning raw summary.")
        return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point.

        python twitter_summary.py              # Basic summary
        python twitter_summary.py --claude     # AI-enhanced summary
        python twitter_summary.py --no-scrape  # Skip profile scraping
    """
    use_claude = "--claude" in sys.argv
    no_scrape  = "--no-scrape" in sys.argv

    print()
    print("=" * 50)
    print("  AI Employee — X (Twitter) Summary (Free)")
    print("=" * 50)
    print()

    PLANS_PATH.mkdir(parents=True, exist_ok=True)

    profile: dict[str, Any] = {}
    if not no_scrape:
        logger.info("Scraping profile from saved session...")
        profile = scrape_profile()

    local_posts = read_local_posted()
    summary     = generate_summary(profile, local_posts)

    if use_claude:
        logger.info("Enhancing summary with Claude...")
        summary = enhance_with_claude(summary)

    now      = datetime.now(tz=PKT)
    date_str = now.strftime("%Y-%m-%d")
    filename = f"TWITTER_SUMMARY_{date_str}.md"
    filepath = PLANS_PATH / filename

    output = (
        f"---\n"
        f"type: twitter_summary\n"
        f"generated_at: {now.isoformat()}\n"
        f"local_posts_found: {len(local_posts)}\n"
        f"ai_enhanced: {use_claude}\n"
        f"---\n\n"
        f"{summary}\n"
    )
    filepath.write_text(output, encoding="utf-8")
    logger.info("Summary saved: %s", filepath)

    print(f"\nSummary saved to: {filepath}")


if __name__ == "__main__":
    main()
