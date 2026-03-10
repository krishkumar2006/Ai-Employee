"""
social_drafter.py — Platinum Tier (Cloud Domain)
===================================================
Cloud-side social media DRAFT generator.

Role in the architecture
------------------------
  Cloud VM (this script):
    1. Picks up social tasks from vault/Needs_Action/social/
    2. Calls Claude to generate platform-specific content
    3. Saves drafts with `status: draft` to:
         vault/Twitter_Drafts/     (tweets / threads)
         vault/Meta_Drafts/        (Facebook / Instagram)
         vault/LinkedIn_Drafts/    (LinkedIn posts)
    4. vault_sync.sh pushes drafts to GitHub

  Local machine (separate poster scripts):
    5. Pulls from GitHub
    6. Human reviews and sets `status: ready`
    7. twitter_poster.py / meta_poster.py / linkedin_poster.py post via
       Playwright (free, no API keys needed)

Why Playwright instead of APIs?
  - Twitter/X API v2: write access costs $100/mo → Playwright is free
  - Meta Graph API: requires app review, business verification → Playwright is free
  - LinkedIn API: posting restricted to approved partners → Playwright is free

Task file format (vault/Needs_Action/social/<filename>.json or .md)
--------------------------------------------------------------------
JSON example:
  {
    "type": "social_request",
    "topic": "5 AI productivity tips for entrepreneurs",
    "platform": "all",        // twitter | meta | linkedin | all
    "tone": "professional",   // professional | casual | thought-leadership
    "domain": "social",
    "ts": "2026-03-02T10:00:00+05:00"
  }

Markdown example:
  ---
  type: social_request
  platform: twitter
  tone: thought-leadership
  ---
  Write a thread about automating repetitive business tasks with AI.

Output drafts
-----------
  vault/Twitter_Drafts/DRAFT_<topic>_<ts>.md   — tweet or thread
  vault/Meta_Drafts/DRAFT_<topic>_<ts>.md       — Facebook/Instagram
  vault/LinkedIn_Drafts/DRAFT_<topic>_<ts>.md   — LinkedIn

  All output drafts have `status: draft` → human sets to `ready` → poster posts.

Usage
-----
  python watchers/social_drafter.py [--poll 120] [--once]

  --poll N  : polling interval in seconds (default 120)
  --once    : process all pending tasks once and exit (cron-friendly)

Prerequisites
-------------
  pip install anthropic python-dotenv
  ANTHROPIC_API_KEY must be set (in .env.cloud or environment)
  DEPLOYMENT_MODE=cloud (social_post_* actions are blocked, draft-only)
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT        = PROJECT_ROOT / "vault"
NEEDS_SOCIAL = VAULT / "Needs_Action" / "social"
DONE_SOCIAL  = VAULT / "Done" / "social"
PLANS_DIR    = VAULT / "Plans" / "social"

TWITTER_DRAFTS  = VAULT / "Twitter_Drafts"
META_DRAFTS     = VAULT / "Meta_Drafts"
LINKEDIN_DRAFTS = VAULT / "LinkedIn_Drafts"

# ---------------------------------------------------------------------------
# Bootstrap: import siblings
# ---------------------------------------------------------------------------
sys.path.insert(0, str(PROJECT_ROOT))

from audit_logger import (
    AuditLogger,
    EV_START, EV_STOP, EV_TASK_CREATED, EV_PLAN_GENERATED, EV_API_CALL,
    EV_API_FAIL, EV_ALERT,
)
from config import cfg, ModeError

# Rate limiter (optional — degrades gracefully if rate_limiter.py missing)
try:
    from rate_limiter import limiter as _limiter, RateLimitError as _RateLimitError
    _RATE_LIMITER_AVAILABLE = True
except ImportError:
    _RATE_LIMITER_AVAILABLE = False

PKT = timezone(timedelta(hours=5))
log = AuditLogger("social_drafter")


# ---------------------------------------------------------------------------
# Claude prompt templates per platform
# ---------------------------------------------------------------------------

PROMPTS: dict[str, str] = {
    "twitter": """You are a social media expert. Write a Twitter/X post for this topic.

Topic: {topic}
Tone: {tone}
Business context: {context}

Rules:
- If the topic suits a single tweet: write one tweet (max 280 characters).
- If it needs more depth: write a thread of 3-7 tweets.
- For threads: separate each tweet with exactly: ---tweet---
- Do NOT add hashtags unless they add real value (max 2).
- No filler phrases like "In today's world..." or "As we all know..."
- Output ONLY the tweet content. No explanations, no meta-commentary.

Output format for single tweet:
<tweet text here>

Output format for thread:
<tweet 1>
---tweet---
<tweet 2>
---tweet---
<tweet 3>""",

    "meta": """You are a social media expert. Write a Facebook/Instagram post for this topic.

Topic: {topic}
Tone: {tone}
Business context: {context}
Platform: Facebook and Instagram

Rules:
- Facebook: conversational, can be longer (150-300 words), end with a question to boost engagement.
- Instagram caption: punchy opening line (first 125 chars are what people see before "more"),
  then 2-3 sentences max, then 5-10 relevant hashtags.
- Write BOTH versions, separated by: ---instagram---
- Output ONLY the post content. No explanations.

Output format:
<Facebook post here>
---instagram---
<Instagram caption here>""",

    "linkedin": """You are a professional LinkedIn content writer. Write a LinkedIn post for this topic.

Topic: {topic}
Tone: {tone}
Business context: {context}

Rules:
- Hook: first line must stop the scroll (bold claim, surprising fact, or question).
- Body: 150-250 words. Use short paragraphs (1-2 sentences each).
- Tone: {tone}. Avoid corporate jargon and buzzwords.
- End with ONE clear call to action or question.
- Add 3-5 relevant hashtags at the end.
- Output ONLY the post content. No explanations.

Output format:
<LinkedIn post here>""",
}

PLATFORM_MAP: dict[str, list[str]] = {
    "twitter":   ["twitter"],
    "meta":      ["meta"],
    "facebook":  ["meta"],
    "instagram": ["meta"],
    "linkedin":  ["linkedin"],
    "all":       ["twitter", "meta", "linkedin"],
}


# ---------------------------------------------------------------------------
# Task parsing
# ---------------------------------------------------------------------------

def parse_task(path: Path) -> Optional[dict[str, Any]]:
    """Parse a social task file (.json or .md with YAML frontmatter)."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception as e:
        log.error(EV_API_FAIL, action="read_task", task=path.name, error=str(e))
        return None

    task: dict[str, Any] = {}

    if path.suffix.lower() == ".json":
        try:
            task = json.loads(text)
        except json.JSONDecodeError as e:
            log.error(EV_API_FAIL, action="parse_json", task=path.name, error=str(e))
            return None
        topic = task.get("topic") or task.get("subject") or task.get("description", "")

    else:
        # Markdown with optional YAML frontmatter
        meta: dict[str, str] = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        k, _, v = line.partition(":")
                        meta[k.strip()] = v.strip()
                body = parts[2].strip()
        task.update(meta)
        topic = body or meta.get("topic", "")

    if not topic:
        log.warn(EV_ALERT, action="skip_empty_task", task=path.name)
        return None

    platforms_raw = task.get("platform", "all")
    platforms = PLATFORM_MAP.get(platforms_raw.lower(), ["twitter", "meta", "linkedin"])

    return {
        "path":      path,
        "topic":     topic,
        "platforms": platforms,
        "tone":      task.get("tone", cfg.get("SOCIAL_DRAFT_DEFAULT_TONE", "professional")),
        "context":   task.get("context", task.get("business_context", "")),
    }


# ---------------------------------------------------------------------------
# Claude draft generation
# ---------------------------------------------------------------------------

def generate_draft(topic: str, platform: str, tone: str, context: str) -> Optional[str]:
    """
    Call Claude CLI to generate a social media draft.
    Returns the generated text or None on failure.
    """
    # Dry-run: return a placeholder instead of calling Claude
    if cfg.is_dry_run():
        print(f"  [social_drafter] [DRY RUN] Would generate {platform} draft for: {topic[:60]}")
        return None

    # Rate limit check before calling Claude
    if _RATE_LIMITER_AVAILABLE:
        try:
            _limiter.check_and_record("social_draft")
            _limiter.check_and_record("claude_call")
        except _RateLimitError as e:
            log.warn(EV_API_FAIL, service="claude", reason="rate_limited",
                     platform=platform, error=str(e))
            print(f"  [social_drafter] RATE LIMIT: {e}")
            return None

    prompt_template = PROMPTS.get(platform, PROMPTS["twitter"])
    prompt = prompt_template.format(topic=topic, tone=tone, context=context or "N/A")

    log.info(EV_API_CALL, service="claude", action="generate_draft",
             platform=platform, topic=topic[:80])
    try:
        result = subprocess.run(
            ["claude", "--print", "--prompt", prompt],
            capture_output=True, text=True,
            timeout=int(cfg.get("CLAUDE_TIMEOUT", "180")),
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0 and result.stdout.strip():
            log.info(EV_PLAN_GENERATED, platform=platform, chars=len(result.stdout))
            return result.stdout.strip()
        else:
            log.error(EV_API_FAIL, service="claude", platform=platform,
                      code=result.returncode, stderr=result.stderr[:300])
            return None

    except FileNotFoundError:
        log.warn(EV_API_FAIL, service="claude", reason="cli_not_found")
        return None
    except subprocess.TimeoutExpired:
        log.error(EV_API_FAIL, service="claude", reason="timeout", platform=platform)
        return None
    except Exception as exc:
        log.exception(EV_API_FAIL, exc, service="claude", platform=platform)
        return None


# ---------------------------------------------------------------------------
# Draft file writers
# ---------------------------------------------------------------------------

def _safe_stem(topic: str) -> str:
    """Create a safe filename stem from a topic string."""
    return (
        topic[:40]
        .replace("/", "-").replace("\\", "-").replace(":", "-")
        .replace("*", "").replace("?", "").replace('"', "").replace("|", "-")
        .strip()
    )


def _timestamp() -> str:
    return datetime.now(tz=PKT).strftime("%Y-%m-%dT%H-%M-%S")


def write_twitter_draft(content: str, topic: str, tone: str) -> Path:
    """Write a Twitter draft .md file with status: draft."""
    TWITTER_DRAFTS.mkdir(parents=True, exist_ok=True)
    is_thread = "---tweet---" in content
    filename  = f"DRAFT_TWITTER_{_safe_stem(topic)}_{_timestamp()}.md"
    path      = TWITTER_DRAFTS / filename

    frontmatter = (
        f"---\n"
        f"type: twitter_draft\n"
        f"post_type: {'thread' if is_thread else 'tweet'}\n"
        f"status: draft\n"
        f"tone: {tone}\n"
        f"topic: {topic[:120]}\n"
        f"generated_at: {datetime.now(tz=PKT).isoformat()}\n"
        f"generated_by: social_drafter (cloud)\n"
        f"---\n\n"
    )
    path.write_text(frontmatter + content, encoding="utf-8")
    log.info(EV_TASK_CREATED, action="draft_written", platform="twitter",
             file=filename, thread=is_thread, chars=len(content))
    return path


def write_meta_draft(content: str, topic: str, tone: str) -> Path:
    """Write a Meta (FB + IG) draft .md file with status: draft."""
    META_DRAFTS.mkdir(parents=True, exist_ok=True)
    filename = f"DRAFT_META_{_safe_stem(topic)}_{_timestamp()}.md"
    path     = META_DRAFTS / filename

    # Split FB and IG content if both were generated
    fb_content = content
    ig_content = ""
    if "---instagram---" in content:
        parts      = content.split("---instagram---", 1)
        fb_content = parts[0].strip()
        ig_content = parts[1].strip()

    # Default to text post; mark as photo if user adds image_path later
    frontmatter = (
        f"---\n"
        f"type: meta_draft\n"
        f"platform: both\n"
        f"post_type: text\n"
        f"status: draft\n"
        f"tone: {tone}\n"
        f"topic: {topic[:120]}\n"
        f"generated_at: {datetime.now(tz=PKT).isoformat()}\n"
        f"generated_by: social_drafter (cloud)\n"
        f"# image_path: /path/to/image.jpg   ← uncomment and add path for photo post\n"
        f"---\n\n"
        f"<!-- FACEBOOK -->\n"
    )
    body = fb_content
    if ig_content:
        body += f"\n\n<!-- INSTAGRAM -->\n{ig_content}"

    path.write_text(frontmatter + body, encoding="utf-8")
    log.info(EV_TASK_CREATED, action="draft_written", platform="meta",
             file=filename, chars=len(content))
    return path


def write_linkedin_draft(content: str, topic: str, tone: str) -> Path:
    """Write a LinkedIn draft .md file with status: draft."""
    LINKEDIN_DRAFTS.mkdir(parents=True, exist_ok=True)
    filename = f"DRAFT_LINKEDIN_{_safe_stem(topic)}_{_timestamp()}.md"
    path     = LINKEDIN_DRAFTS / filename

    frontmatter = (
        f"---\n"
        f"type: linkedin_draft\n"
        f"status: draft\n"
        f"tone: {tone}\n"
        f"topic: {topic[:120]}\n"
        f"generated_at: {datetime.now(tz=PKT).isoformat()}\n"
        f"generated_by: social_drafter (cloud)\n"
        f"---\n\n"
    )
    path.write_text(frontmatter + content, encoding="utf-8")
    log.info(EV_TASK_CREATED, action="draft_written", platform="linkedin",
             file=filename, chars=len(content))
    return path


DRAFT_WRITERS = {
    "twitter":  write_twitter_draft,
    "meta":     write_meta_draft,
    "linkedin": write_linkedin_draft,
}


# ---------------------------------------------------------------------------
# Process one task
# ---------------------------------------------------------------------------

def process_task(task: dict[str, Any]) -> int:
    """Generate drafts for all platforms in the task. Returns count of drafts written."""
    topic     = task["topic"]
    platforms = task["platforms"]
    tone      = task["tone"]
    context   = task["context"]
    src_path  = task["path"]

    print(f"  [social_drafter] Topic: {topic[:60]}...")
    print(f"  Platforms: {', '.join(platforms)} | Tone: {tone}")

    drafts_written = 0
    for platform in platforms:
        content = generate_draft(topic, platform, tone, context)
        if content:
            writer = DRAFT_WRITERS.get(platform)
            if writer:
                out_path = writer(content, topic, tone)
                print(f"  [draft saved] {platform}: {out_path.name}")
                drafts_written += 1
        else:
            log.warn(EV_API_FAIL, action="draft_skipped",
                     platform=platform, topic=topic[:80])
            print(f"  [WARN] Failed to generate draft for {platform}")

    # Move source task to Done/social/
    if drafts_written > 0:
        DONE_SOCIAL.mkdir(parents=True, exist_ok=True)
        dest = DONE_SOCIAL / src_path.name
        try:
            src_path.rename(dest)
            print(f"  [done] Task moved to Done/social/")
        except OSError:
            pass  # already moved (race condition OK)

    return drafts_written


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    # Mode guard: this script is only meaningful on cloud
    # (local can run it too, but there's no reason to draft if you can post directly)
    try:
        cfg.assert_allowed("social_draft", "social_drafter")
    except ModeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Cloud-side social media draft generator")
    parser.add_argument("--poll", type=int,
                        default=int(cfg.get("SOCIAL_DRAFT_POLL", "120")),
                        help="Poll interval in seconds (default 120)")
    parser.add_argument("--once", action="store_true",
                        help="Process all pending tasks once and exit")
    args = parser.parse_args()

    # Ensure folders exist
    for d in [NEEDS_SOCIAL, DONE_SOCIAL, TWITTER_DRAFTS, META_DRAFTS,
              LINKEDIN_DRAFTS, PLANS_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 60)
    print("  AI Employee — Social Drafter (Cloud Mode)")
    print("=" * 60)
    print(f"  Mode   : {cfg.mode.upper()}")
    print(f"  Input  : vault/Needs_Action/social/")
    print(f"  Output : vault/Twitter_Drafts/, Meta_Drafts/, LinkedIn_Drafts/")
    print(f"  Poll   : every {args.poll}s")
    print("  DRAFTS ONLY — posting happens on LOCAL machine via Playwright")
    print("=" * 60)
    print()

    log.info(EV_START, mode=cfg.mode, poll=args.poll)

    def run_once() -> int:
        tasks = [f for f in NEEDS_SOCIAL.iterdir()
                 if f.suffix.lower() in {".json", ".md"}
                 and not f.name.startswith(".")]
        if not tasks:
            return 0
        total = 0
        for task_file in sorted(tasks):
            task = parse_task(task_file)
            if task:
                total += process_task(task)
        return total

    if args.once:
        n = run_once()
        print(f"Done. Generated {n} draft(s).")
        log.info(EV_STOP, reason="--once", drafts_generated=n)
        return

    try:
        while True:
            run_once()
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log.info(EV_STOP, reason="keyboard_interrupt")
        print("\n[social_drafter] stopped.")


if __name__ == "__main__":
    main()
