"""
Meta Summary — Free Edition (Local Vault Logs)
=============================================================
Reads recently posted content from vault/Meta_Posted/ logs and
generates a markdown summary. No API keys or tokens required.

Can be run:
  - On-demand:  python meta_summary.py
  - Scheduled:  called by orchestrator
  - With Claude: python meta_summary.py --claude

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
POSTED_PATH = VAULT_PATH / "Meta_Posted"

PKT = timezone(timedelta(hours=5))

load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("meta_summary")


# ---------------------------------------------------------------------------
# Local Vault Log Reader
# ---------------------------------------------------------------------------
def read_local_posts() -> dict[str, list[dict[str, Any]]]:
    """Read recently posted content from vault/Meta_Posted/ log files."""
    results: dict[str, list[dict[str, Any]]] = {"facebook": [], "instagram": []}

    if not POSTED_PATH.exists():
        return results

    for f in sorted(POSTED_PATH.glob("META_*.md"), reverse=True)[:40]:
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
                    if body.startswith("##"):
                        body = "\n".join(body.splitlines()[1:]).strip()

            platform  = metadata.get("platform", "facebook")
            status    = metadata.get("status", "")
            posted_at = metadata.get("posted_at", "")
            post_type = metadata.get("post_type", "text")

            entry = {
                "filename":  f.name,
                "posted_at": posted_at,
                "post_type": post_type,
                "status":    status,
                "body":      body[:150],
            }

            if platform in results:
                results[platform].append(entry)

        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# Summary Generation
# ---------------------------------------------------------------------------
def generate_summary(posts: dict[str, list[dict[str, Any]]]) -> str:
    """Build a markdown summary of Meta posting activity."""
    now = datetime.now(tz=PKT)
    lines: list[str] = []

    lines.append("# Meta Social Media Summary")
    lines.append("")
    lines.append(f"> Generated: {now.strftime('%Y-%m-%d %H:%M:%S')} PKT")
    lines.append(f"> Method: Local vault logs (Playwright — free, no API key)")
    lines.append("")
    lines.append(
        "> ℹ️ Engagement metrics (likes, comments, shares) require Meta Graph API."
    )
    lines.append("> This summary tracks posting activity from vault/Meta_Posted/.")
    lines.append("")

    # ----- Facebook -----
    fb_posts = posts.get("facebook", [])
    lines.append("## Facebook")
    lines.append("")

    if fb_posts:
        posted_fb = [p for p in fb_posts if p["status"] == "posted"]
        failed_fb = [p for p in fb_posts if p["status"] == "failed"]

        lines.append(f"- **Total posted:** {len(posted_fb)}")
        lines.append(f"- **Failed:** {len(failed_fb)}")
        lines.append("")

        by_type: dict[str, int] = {}
        for p in posted_fb:
            by_type[p["post_type"]] = by_type.get(p["post_type"], 0) + 1
        if by_type:
            lines.append("**By type:** " + ", ".join(f"{v} {k}" for k, v in by_type.items()))
            lines.append("")

        if posted_fb:
            lines.append("### Recent Posts (last 10)")
            lines.append("")
            for post in posted_fb[:10]:
                ts = post["posted_at"][:19] if len(post["posted_at"]) >= 19 else post["posted_at"]
                lines.append(f"**{ts}** | `{post['post_type']}`")
                if post["body"]:
                    preview = post["body"][:100] + "..." if len(post["body"]) > 100 else post["body"]
                    lines.append(f"> {preview}")
                lines.append("")
    else:
        lines.append("*No Facebook posts found in vault/Meta_Posted/ yet.*")
        lines.append("")

    # ----- Instagram -----
    ig_posts = posts.get("instagram", [])
    lines.append("## Instagram")
    lines.append("")

    if ig_posts:
        posted_ig = [p for p in ig_posts if p["status"] == "posted"]
        failed_ig = [p for p in ig_posts if p["status"] == "failed"]

        lines.append(f"- **Total posted:** {len(posted_ig)}")
        lines.append(f"- **Failed:** {len(failed_ig)}")
        lines.append("")

        by_type_ig: dict[str, int] = {}
        for p in posted_ig:
            by_type_ig[p["post_type"]] = by_type_ig.get(p["post_type"], 0) + 1
        if by_type_ig:
            lines.append("**By type:** " + ", ".join(f"{v} {k}" for k, v in by_type_ig.items()))
            lines.append("")

        if posted_ig:
            lines.append("### Recent Posts (last 10)")
            lines.append("")
            for post in posted_ig[:10]:
                ts = post["posted_at"][:19] if len(post["posted_at"]) >= 19 else post["posted_at"]
                lines.append(f"**{ts}** | `{post['post_type']}`")
                if post["body"]:
                    preview = post["body"][:100] + "..." if len(post["body"]) > 100 else post["body"]
                    lines.append(f"> {preview}")
                lines.append("")
    else:
        lines.append("*No Instagram posts found in vault/Meta_Posted/ yet.*")
        lines.append("")

    return "\n".join(lines)


def enhance_with_claude(summary: str) -> str:
    """Send raw summary to Claude for AI-enhanced insights."""
    prompt = (
        "You are the AI Employee social media analyst.\n"
        "Below is a Meta (Facebook + Instagram) posting activity summary.\n"
        "Add a section called '## AI Insights' with:\n"
        "- Best times to post based on the log\n"
        "- Content type breakdown and recommendations\n"
        "- 3 actionable tips for better Meta engagement\n"
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
        logger.warning("Claude enhancement failed.")
        return summary
    except Exception:
        logger.warning("Claude CLI not available.")
        return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point.

        python meta_summary.py              # Basic summary
        python meta_summary.py --claude     # AI-enhanced summary
    """
    use_claude = "--claude" in sys.argv

    print()
    print("=" * 50)
    print("  AI Employee — Meta Summary (Free)")
    print("=" * 50)
    print()

    PLANS_PATH.mkdir(parents=True, exist_ok=True)

    posts   = read_local_posts()
    summary = generate_summary(posts)

    if use_claude:
        logger.info("Enhancing with Claude...")
        summary = enhance_with_claude(summary)

    now      = datetime.now(tz=PKT)
    date_str = now.strftime("%Y-%m-%d")
    filename = f"META_SUMMARY_{date_str}.md"
    filepath = PLANS_PATH / filename

    fb_count = len(posts.get("facebook", []))
    ig_count = len(posts.get("instagram", []))

    output = (
        f"---\n"
        f"type: meta_social_summary\n"
        f"generated_at: {now.isoformat()}\n"
        f"fb_posts_logged: {fb_count}\n"
        f"ig_posts_logged: {ig_count}\n"
        f"ai_enhanced: {use_claude}\n"
        f"---\n\n"
        f"{summary}\n"
    )
    filepath.write_text(output, encoding="utf-8")
    logger.info("Summary saved: %s", filepath)

    print(f"\nSummary saved to: {filepath}")


if __name__ == "__main__":
    main()
