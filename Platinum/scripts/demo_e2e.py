"""
demo_e2e.py — Platinum Tier End-to-End Demo
=============================================
Demonstrates the complete delegation pipeline from email arrival to sent reply.

Pipeline (10 phases):
  1  Offline simulation   — mark local orchestrator as offline
  2  Email arrives        — task card written to Needs_Action/email/
  3  Cloud draft          — Claude drafts reply (or uses fixture)
  4  Approval file        — draft + metadata written to Pending_Approval/email/
  5  Vault sync           — git push cloud→ pull local (or file-copy sim)
  6  Local comes online   — offline marker removed
  7  Approval watcher     — claim-by-move; auto or human decision
  8  Human approves       — interactive prompt or --auto-approve
  9  Execute send         — Gmail SMTP (or --dry-run to skip real send)
  10 Audit + Done         — move card to Done/email/, audit log, rate counter

Usage
-----
  python scripts/demo_e2e.py                          # interactive demo
  python scripts/demo_e2e.py --auto-approve           # no prompts
  python scripts/demo_e2e.py --dry-run                # no real email sent
  python scripts/demo_e2e.py --no-claude              # use fixture draft
  python scripts/demo_e2e.py --no-git                 # skip git push/pull
  python scripts/demo_e2e.py --from-phase 5           # resume from phase 5
  python scripts/demo_e2e.py --to EMAIL@EXAMPLE.COM   # override recipient

Requirements (all free)
-----------------------
  pip install (none needed — stdlib only)
  GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env.local (for real send)
  ANTHROPIC_API_KEY in env (for Claude draft)
  git configured with remote (for vault sync, or use --no-git)
"""

import argparse
import json
import os
import re
import shutil
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from config import cfg
    _HAS_CONFIG = True
except ImportError:
    cfg = None
    _HAS_CONFIG = False

try:
    from audit_logger import AuditLogger, EV_START, EV_STOP, EV_TASK_CREATED, \
        EV_ODOO_ACTION, EV_API_CALL, EV_ALERT
    log = AuditLogger("demo_e2e")
    _HAS_AUDIT = True
except ImportError:
    log = None
    _HAS_AUDIT = False

try:
    from rate_limiter import limiter as _limiter, RateLimitError
    _HAS_RATE = True
except ImportError:
    _HAS_RATE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT              = PROJECT_ROOT / "vault"
NEEDS_ACTION_EMAIL = VAULT / "Needs_Action" / "email"
PENDING_EMAIL      = VAULT / "Pending_Approval" / "email"
APPROVED_EMAIL     = VAULT / "Approved" / "email"
DONE_EMAIL         = VAULT / "Done" / "email"
IN_PROGRESS_AW     = VAULT / "In_Progress" / "approval_watcher"
LOGS_DIR           = VAULT / "Logs"
PLANS_EMAIL        = VAULT / "Plans" / "email"
DEMO_LOG           = LOGS_DIR / "demo_e2e.log"
OFFLINE_MARKER     = LOGS_DIR / ".orchestrator_offline"

PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# ANSI colours (fallback to plain on Windows without ANSI support)
# ---------------------------------------------------------------------------
_ANSI = sys.stdout.isatty() or os.environ.get("FORCE_COLOR")

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _ANSI else text

def bold(t: str)    -> str: return _c("1", t)
def green(t: str)   -> str: return _c("1;32", t)
def yellow(t: str)  -> str: return _c("1;33", t)
def red(t: str)     -> str: return _c("1;31", t)
def cyan(t: str)    -> str: return _c("1;36", t)
def blue(t: str)    -> str: return _c("1;34", t)
def dim(t: str)     -> str: return _c("2", t)

# ---------------------------------------------------------------------------
# Demo state
# ---------------------------------------------------------------------------
DEMO_STATE: dict = {
    "task_file":     None,   # Path of the Needs_Action email card
    "approval_file": None,   # Path of the Pending_Approval file
    "draft_text":    None,   # Claude-generated reply
    "sender_email":  None,   # From: address from the incoming email
    "sender_name":   None,   # From: display name
    "subject":       None,   # Email subject
    "phases_run":    [],
    "started_at":    datetime.now(tz=PKT).isoformat(),
}


# ---------------------------------------------------------------------------
# Sample email fixture (used when --no-claude or no API key)
# ---------------------------------------------------------------------------
SAMPLE_INCOMING_EMAIL = {
    "from_name":  "Sarah Chen",
    "from_email": "sarah.chen@example-client.com",
    "subject":    "Inquiry about AI automation services for our logistics team",
    "body": (
        "Hi,\n\n"
        "I came across your profile and I'm very interested in understanding how AI\n"
        "automation could help our 12-person logistics team reduce manual data entry.\n\n"
        "Specifically, we spend about 3 hours/day copying data from supplier emails\n"
        "into our ERP system. Could you arrange a 30-minute call this week to discuss\n"
        "whether your system could address this use case?\n\n"
        "Our availability: Tuesday 2–4 PM or Thursday any time after 11 AM (PKT).\n\n"
        "Looking forward to hearing from you.\n\n"
        "Best regards,\n"
        "Sarah Chen\n"
        "Operations Manager, FastMove Logistics"
    ),
    "received_at": datetime.now(tz=PKT).isoformat(),
    "priority": "high",
    "domain": "email",
}

FIXTURE_DRAFT_REPLY = """\
Hi Sarah,

Thank you for reaching out — the challenge you've described is a great fit for what we do.

Manually copying supplier email data into an ERP is one of the most common pain points we solve. Our AI Employee system can:

1. Read incoming supplier emails automatically (Gmail integration)
2. Extract key data fields (quantities, SKUs, prices, dates)
3. Draft the ERP entry for human review before committing
4. Confirm and push to your ERP after approval

For a 12-person team spending 3 hours/day on this, we typically see an 85–90% reduction in manual effort within the first two weeks.

I'd be happy to schedule a call. **Tuesday 2 PM PKT** works perfectly on my end — shall I send a calendar invite?

Looking forward to it.

Best regards,
[Your Name]
AI Employee System
"""


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def ts_str() -> str:
    return datetime.now(tz=PKT).strftime("%Y-%m-%d %H:%M:%S")

def ts_slug() -> str:
    return datetime.now(tz=PKT).strftime("%Y%m%dT%H%M%S")

def demo_log(msg: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[demo_e2e {ts_str()}] {msg}"
    with DEMO_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def pause(label: str = "Continue", auto: bool = False) -> None:
    if auto:
        time.sleep(0.8)
        return
    try:
        input(dim(f"  ↵  Press ENTER to {label}... "))
    except (EOFError, KeyboardInterrupt):
        print()

def banner(phase: int, title: str, subtitle: str = "") -> None:
    width = 62
    print()
    print(cyan("━" * width))
    print(cyan(f"  PHASE {phase:02d}  ") + bold(title))
    if subtitle:
        print(dim(f"  {subtitle}"))
    print(cyan("━" * width))
    demo_log(f"--- Phase {phase}: {title} ---")

def step(msg: str) -> None:
    print(f"  {blue('▶')} {msg}")
    demo_log(f"  step: {msg}")

def ok(msg: str) -> None:
    print(f"  {green('✓')} {msg}")
    demo_log(f"  ok: {msg}")

def warn(msg: str) -> None:
    print(f"  {yellow('⚠')} {msg}")
    demo_log(f"  warn: {msg}")

def err(msg: str) -> None:
    print(f"  {red('✗')} {msg}")
    demo_log(f"  error: {msg}")

def show_file(path: Path, max_lines: int = 20) -> None:
    """Print file contents with a border."""
    if not path.exists():
        warn(f"File not found: {path}")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    print(dim("  ┌" + "─" * 56 + "┐"))
    for line in lines[:max_lines]:
        print(dim("  │ ") + line[:55])
    if len(lines) > max_lines:
        print(dim(f"  │  ... ({len(lines) - max_lines} more lines)"))
    print(dim("  └" + "─" * 56 + "┘"))

def _get_env(key: str, default: str = "") -> str:
    if _HAS_CONFIG:
        return cfg.get(key, default)
    return os.environ.get(key, default)


# ---------------------------------------------------------------------------
# Phase 1: Offline simulation
# ---------------------------------------------------------------------------

def phase1_offline(args) -> None:
    banner(1, "Offline Simulation", "Local orchestrator is stopped / machine is sleeping")
    step("Checking if local orchestrator is running...")

    # Check via process list (cross-platform, no extra deps)
    proc_found = False
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"],
                capture_output=True, text=True
            ).stdout
            proc_found = "orchestrator" in out.lower()
        else:
            out = subprocess.run(
                ["pgrep", "-af", "orchestrator.py"],
                capture_output=True, text=True
            ).stdout
            proc_found = bool(out.strip())
    except Exception:
        pass

    if proc_found:
        warn("Orchestrator process detected — for demo, we'll leave it running")
        warn("In production: orchestrator stop = pm2 stop orchestrator")
    else:
        ok("Orchestrator is NOT running (simulating offline state)")

    step("Writing offline marker file...")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    OFFLINE_MARKER.write_text(json.dumps({
        "offline_since": datetime.now(tz=PKT).isoformat(),
        "reason":        "demo_simulation",
        "note":          "Local machine offline — cloud will continue working",
    }, indent=2), encoding="utf-8")
    ok(f"Marker: {OFFLINE_MARKER.relative_to(PROJECT_ROOT)}")

    step("Cloud side is running on Oracle VM (always-on, unaffected)")
    ok("Cloud continues: gmail_watcher, social_drafter, ralph_loop all running")

    DEMO_STATE["phases_run"].append(1)
    if _HAS_AUDIT:
        log.info(EV_START, action="demo_phase1_offline", marker=str(OFFLINE_MARKER))


# ---------------------------------------------------------------------------
# Phase 2: Email arrives
# ---------------------------------------------------------------------------

def phase2_email_arrives(args, email_data: dict) -> Path:
    banner(2, "Email Arrives", "gmail_watcher creates a task card in Needs_Action/email/")

    NEEDS_ACTION_EMAIL.mkdir(parents=True, exist_ok=True)

    subject_slug = re.sub(r"[^a-z0-9]+", "_", email_data["subject"].lower())[:40]
    filename     = f"EMAIL_{ts_slug()}_{subject_slug}.json"
    card_path    = NEEDS_ACTION_EMAIL / filename

    card = {
        "type":        "email_task",
        "domain":      "email",
        "priority":    email_data["priority"],
        "status":      "pending",
        "from_name":   email_data["from_name"],
        "from_email":  email_data["from_email"],
        "subject":     email_data["subject"],
        "body":        email_data["body"],
        "received_at": email_data["received_at"],
        "created_by":  "gmail_watcher",
        "demo":        True,
    }
    card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")

    step(f"Task card created: {card_path.relative_to(PROJECT_ROOT)}")
    ok(f"From   : {email_data['from_name']} <{email_data['from_email']}>")
    ok(f"Subject: {email_data['subject']}")
    ok(f"Priority: {email_data['priority'].upper()}")
    print()
    step("Card preview:")
    show_file(card_path, max_lines=12)

    DEMO_STATE["task_file"]    = card_path
    DEMO_STATE["sender_email"] = email_data["from_email"]
    DEMO_STATE["sender_name"]  = email_data["from_name"]
    DEMO_STATE["subject"]      = email_data["subject"]

    DEMO_STATE["phases_run"].append(2)
    if _HAS_AUDIT:
        log.info(EV_TASK_CREATED, action="demo_email_task", file=filename,
                 from_email=email_data["from_email"])

    return card_path


# ---------------------------------------------------------------------------
# Phase 3: Cloud drafts reply
# ---------------------------------------------------------------------------

def phase3_cloud_draft(args, card_path: Path, email_data: dict) -> str:
    banner(3, "Cloud Drafts Reply", "Claude generates a professional reply on Oracle VM")

    if args.no_claude:
        step("--no-claude flag set → using fixture draft (no API call)")
        draft = FIXTURE_DRAFT_REPLY
        ok(f"Fixture draft loaded ({len(draft)} chars)")
    else:
        api_key = _get_env("ANTHROPIC_API_KEY")
        if not api_key:
            warn("ANTHROPIC_API_KEY not set — falling back to fixture draft")
            draft = FIXTURE_DRAFT_REPLY
        else:
            step("Calling Claude CLI to draft reply...")
            prompt = (
                f"You are a professional AI assistant replying to a business email.\n\n"
                f"--- INCOMING EMAIL ---\n"
                f"From: {email_data['from_name']} <{email_data['from_email']}>\n"
                f"Subject: {email_data['subject']}\n\n"
                f"{email_data['body']}\n"
                f"--- END EMAIL ---\n\n"
                f"Write a professional, warm, and concise reply that:\n"
                f"1. Acknowledges their specific need\n"
                f"2. Briefly explains how our AI automation system addresses it\n"
                f"3. Proposes a specific time for the call they requested\n"
                f"4. Ends with a clear next step\n\n"
                f"Keep it under 200 words. Output ONLY the email body (no subject line)."
            )
            try:
                timeout = int(_get_env("CLAUDE_TIMEOUT", "120"))
                import sys as _sys
                _claude_cmd = "claude.cmd" if _sys.platform == "win32" else "claude"
                result  = subprocess.run(
                    [_claude_cmd, "--print", "--prompt", prompt],
                    capture_output=True, text=True,
                    timeout=timeout, cwd=str(PROJECT_ROOT),
                    shell=(_sys.platform == "win32"),
                )
                if result.returncode == 0 and result.stdout.strip():
                    draft = result.stdout.strip()
                    ok(f"Claude generated reply ({len(draft)} chars)")
                    if _HAS_RATE:
                        try:
                            _limiter.check_and_record("claude_call")
                            _limiter.check_and_record("email_draft")
                        except RateLimitError as e:
                            warn(f"Rate limit note: {e}")
                else:
                    warn(f"Claude returned code {result.returncode} — using fixture")
                    draft = FIXTURE_DRAFT_REPLY
            except FileNotFoundError:
                warn("claude CLI not found — using fixture draft")
                draft = FIXTURE_DRAFT_REPLY
            except subprocess.TimeoutExpired:
                warn("Claude timed out — using fixture draft")
                draft = FIXTURE_DRAFT_REPLY

    print()
    step("Draft preview:")
    for line in draft.splitlines()[:12]:
        print(dim(f"    {line}"))
    if draft.count("\n") > 12:
        print(dim(f"    ... ({draft.count(chr(10)) - 12} more lines)"))

    DEMO_STATE["draft_text"]   = draft
    DEMO_STATE["phases_run"].append(3)
    if _HAS_AUDIT:
        log.info(EV_API_CALL, action="demo_draft", chars=len(draft),
                 via="claude" if not args.no_claude else "fixture")

    return draft


# ---------------------------------------------------------------------------
# Phase 4: Approval file written
# ---------------------------------------------------------------------------

def phase4_write_approval(args, card_path: Path, email_data: dict, draft: str) -> Path:
    banner(4, "Approval File Written",
           "Cloud writes draft + metadata to Pending_Approval/email/")

    PENDING_EMAIL.mkdir(parents=True, exist_ok=True)

    card_stem    = card_path.stem
    approval_file = PENDING_EMAIL / f"DRAFT_{card_stem}.json"

    payload = {
        "type":          "email_draft_approval",
        "domain":        "email",
        "priority":      email_data["priority"],
        "status":        "pending_approval",
        "action":        "send_email",
        "to_email":      email_data["from_email"],
        "to_name":       email_data["from_name"],
        "subject":       f"Re: {email_data['subject']}",
        "draft_body":    draft,
        "source_card":   card_path.name,
        "created_by":    "cloud_agent",
        "created_at":    datetime.now(tz=PKT).isoformat(),
        "requires":      "local_send",
        "demo":          True,
        "instructions":  (
            "Review the draft_body above. "
            "Approve to send via Gmail SMTP. "
            "Reject to discard."
        ),
    }
    approval_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    step(f"Approval file: {approval_file.relative_to(PROJECT_ROOT)}")
    ok(f"To     : {email_data['from_email']}")
    ok(f"Subject: Re: {email_data['subject']}")
    ok(f"Action : send_email (requires local execution)")
    print()
    step("Approval file preview:")
    show_file(approval_file, max_lines=15)

    # Also publish an update (non-blocking)
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "watchers"))
        from update_publisher import publish_update
        publish_update(
            component="cloud_agent",
            event="draft_created",
            domain="email",
            summary=f"Email reply drafted for {email_data['from_name']} — awaiting local approval",
            data={"approval_file": approval_file.name, "to": email_data["from_email"]},
        )
    except Exception:
        pass  # update_publisher is optional in demo context

    DEMO_STATE["approval_file"] = approval_file
    DEMO_STATE["phases_run"].append(4)
    if _HAS_AUDIT:
        log.info(EV_TASK_CREATED, action="demo_approval_file", file=approval_file.name)

    return approval_file


# ---------------------------------------------------------------------------
# Phase 5: Vault sync simulation
# ---------------------------------------------------------------------------

def phase5_vault_sync(args) -> None:
    banner(5, "Vault Sync",
           "Cloud pushes to GitHub → local pulls (or file-copy simulation)")

    if args.no_git:
        step("--no-git flag set → simulating sync (files already in place)")
        ok("In production: vault_sync.sh runs every 5 min via cron")
        ok("  git add vault/Pending_Approval/")
        ok("  git commit -m 'vault-sync: auto ...'")
        ok("  git push origin main")
        ok("  (local) git pull --rebase origin main")
        DEMO_STATE["phases_run"].append(5)
        return

    # Try a real git pull (for demo on same machine)
    step("Attempting real git operations (same-machine demo)...")
    try:
        # Stage the Pending_Approval file
        result = subprocess.run(
            ["git", "add", "vault/Pending_Approval/"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT)
        )
        if result.returncode == 0:
            ok("git add vault/Pending_Approval/ — staged")
        else:
            warn(f"git add issue: {result.stderr.strip()[:80]}")

        # Check if there's anything to commit
        diff = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT)
        )
        if diff.stdout.strip():
            commit = subprocess.run(
                ["git", "commit", "-m", f"demo: email approval pending {ts_slug()}",
                 "--author=DemoBot <demo@ai-employee.local>"],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT)
            )
            if commit.returncode == 0:
                ok(f"git commit: {commit.stdout.strip()[:60]}")
            else:
                warn(f"git commit: {commit.stderr.strip()[:80]}")

            # Push (may fail if remote not configured — that's OK for demo)
            push = subprocess.run(
                ["git", "push", "origin", "main"],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=30
            )
            if push.returncode == 0:
                ok("git push origin main — cloud → remote ✓")
                ok("git pull (simulated — same machine, file already here)")
            else:
                warn("git push failed (remote may not be configured) — files already local")
                warn("In production: cloud VM pushes, local machine pulls")
        else:
            ok("Nothing new to commit (files already tracked)")
    except subprocess.TimeoutExpired:
        warn("git push timed out — treating as sync-simulated")
    except FileNotFoundError:
        warn("git not found — treating as sync-simulated (files already in place)")
    except Exception as exc:
        warn(f"Git error: {exc} — treating as sync-simulated")

    DEMO_STATE["phases_run"].append(5)


# ---------------------------------------------------------------------------
# Phase 6: Local comes online
# ---------------------------------------------------------------------------

def phase6_local_online(args) -> None:
    banner(6, "Local Machine Comes Online", "Orchestrator restarts; approval_watcher begins polling")

    step("Removing offline marker...")
    if OFFLINE_MARKER.exists():
        OFFLINE_MARKER.unlink()
        ok(f"Removed: {OFFLINE_MARKER.name}")
    else:
        ok("Offline marker already cleared")

    step("Local watchers resuming:")
    ok("  approval_watcher.py — polling Pending_Approval/ every 15s")
    ok("  update_merger.py    — polling Updates/ for cloud broadcasts")
    ok("  whatsapp_watcher.py — reconnecting to WhatsApp session")
    ok("  twitter/meta/linkedin_poster.py — ready to post drafts")

    DEMO_STATE["phases_run"].append(6)
    if _HAS_AUDIT:
        log.info(EV_START, action="demo_phase6_local_online")


# ---------------------------------------------------------------------------
# Phase 7: Approval watcher finds the file
# ---------------------------------------------------------------------------

def phase7_approval_watcher(args, approval_file: Path) -> Optional[Path]:
    banner(7, "Approval Watcher Activates",
           "approval_watcher.py claims the pending file via atomic rename")

    step(f"Scanning Pending_Approval/email/ ...")
    ok(f"Found: {approval_file.name}")

    step("Claiming via atomic rename → In_Progress/approval_watcher/")
    IN_PROGRESS_AW.mkdir(parents=True, exist_ok=True)
    claimed_path = IN_PROGRESS_AW / approval_file.name

    if args.dry_run:
        step("[DRY RUN] Would rename — leaving file in Pending_Approval/")
        ok("Dry-run claim simulated")
        DEMO_STATE["phases_run"].append(7)
        return approval_file   # return original so phase 8 still works

    if approval_file.exists():
        approval_file.rename(claimed_path)
        ok(f"Claimed: {claimed_path.relative_to(PROJECT_ROOT)}")
    else:
        warn(f"Approval file already moved: {approval_file.name}")
        if claimed_path.exists():
            ok(f"Already in In_Progress: {claimed_path.name}")
        else:
            err("File missing — it may have been processed already")
            return None

    # Read and display draft for review
    try:
        data = json.loads(claimed_path.read_text(encoding="utf-8"))
        print()
        step("Draft details:")
        ok(f"  To     : {data.get('to_name')} <{data.get('to_email')}>")
        ok(f"  Subject: {data.get('subject')}")
        ok(f"  Priority: {data.get('priority', 'medium').upper()}")
        print()
        step("Draft body:")
        body = data.get("draft_body", "")
        for line in body.splitlines()[:15]:
            print(dim(f"    {line}"))
        if body.count("\n") > 15:
            print(dim(f"    ... ({body.count(chr(10)) - 15} more lines)"))
    except Exception as exc:
        warn(f"Could not read draft: {exc}")

    DEMO_STATE["phases_run"].append(7)
    if _HAS_AUDIT:
        log.info(EV_TASK_CREATED, action="demo_claimed", file=approval_file.name)

    return claimed_path


# ---------------------------------------------------------------------------
# Phase 8: Human approval
# ---------------------------------------------------------------------------

def phase8_approve(args, claimed_path: Path) -> bool:
    banner(8, "Human Reviews and Approves",
           "Human reads the draft and decides to approve or reject")

    if args.dry_run:
        step("[DRY RUN] Simulating approval (no real action)")
        ok("Would move to Approved/email/")
        DEMO_STATE["phases_run"].append(8)
        return True

    if args.auto_approve:
        step("--auto-approve flag set → approving automatically")
        approved = True
    else:
        print()
        print(cyan("  ┌─────────────────────────────────────────────┐"))
        print(cyan("  │  Do you approve sending this reply?         │"))
        print(cyan("  │  [y] Yes, send it   [n] No, reject          │"))
        print(cyan("  └─────────────────────────────────────────────┘"))
        print()
        try:
            answer = input("  Your decision [y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        approved = answer in ("y", "yes")

    if approved:
        # Move to Approved/email/
        APPROVED_EMAIL.mkdir(parents=True, exist_ok=True)
        dest = APPROVED_EMAIL / claimed_path.name
        if args.dry_run:
            ok(f"[DRY RUN] Would move to {dest.relative_to(PROJECT_ROOT)}")
        else:
            if claimed_path.exists():
                claimed_path.rename(dest)
                ok(f"Approved: {dest.relative_to(PROJECT_ROOT)}")
            else:
                warn("Claimed file not found (may already be moved)")
                dest = claimed_path  # fall back
        if _HAS_AUDIT:
            log.info(EV_ODOO_ACTION, action="demo_human_approved",
                     file=claimed_path.name)
        DEMO_STATE["approved_path"] = dest
    else:
        # Reject
        from pathlib import Path as _Path
        rejected_dir = VAULT / "Rejected" / "email"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        dest = rejected_dir / claimed_path.name
        if claimed_path.exists():
            claimed_path.rename(dest)
        warn(f"Rejected → {dest.relative_to(PROJECT_ROOT)}")
        if _HAS_AUDIT:
            log.info(EV_ODOO_ACTION, action="demo_human_rejected",
                     file=claimed_path.name)

    DEMO_STATE["phases_run"].append(8)
    return approved


# ---------------------------------------------------------------------------
# Phase 9: Execute send
# ---------------------------------------------------------------------------

def send_gmail(to_email: str, to_name: str, subject: str, body: str,
               dry_run: bool = False) -> bool:
    """Send an email via Gmail SMTP SSL (port 465). Returns True on success."""
    gmail_address = _get_env("GMAIL_ADDRESS")
    app_password  = _get_env("GMAIL_APP_PASSWORD")

    if dry_run:
        print(f"  {yellow('[DRY RUN]')} Would send email:")
        print(dim(f"    From   : {gmail_address or 'GMAIL_ADDRESS not set'}"))
        print(dim(f"    To     : {to_name} <{to_email}>"))
        print(dim(f"    Subject: {subject}"))
        print(dim(f"    Body   : {len(body)} chars"))
        return True

    if not gmail_address or not app_password:
        warn("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set in .env.local")
        warn("Set them to enable real email sending (free — uses Gmail App Password)")
        return False

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"AI Employee <{gmail_address}>"
    msg["To"]      = f"{to_name} <{to_email}>"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(gmail_address, app_password)
            server.sendmail(gmail_address, [to_email], msg.as_string())
        return True
    except smtplib.SMTPAuthenticationError:
        err("Gmail authentication failed — check GMAIL_APP_PASSWORD")
        err("Get App Password: Google Account → Security → 2-Step Verification → App passwords")
        return False
    except smtplib.SMTPException as e:
        err(f"SMTP error: {e}")
        return False
    except Exception as e:
        err(f"Send failed: {e}")
        return False


def phase9_execute_send(args) -> bool:
    banner(9, "Execute Send via Gmail SMTP",
           "Email sent using stdlib smtplib — no paid API needed")

    approved_path: Optional[Path] = DEMO_STATE.get("approved_path")
    if not approved_path:
        # Try to find it
        approved_path = DEMO_STATE.get("approval_file")

    if not approved_path or not (approved_path.exists() if not args.dry_run else True):
        # Read from DEMO_STATE
        to_email = DEMO_STATE.get("sender_email", "")
        to_name  = DEMO_STATE.get("sender_name", "Sarah Chen")
        subject  = f"Re: {DEMO_STATE.get('subject', 'Your inquiry')}"
        body     = DEMO_STATE.get("draft_text", FIXTURE_DRAFT_REPLY)
    else:
        # Read from approved file
        try:
            data = json.loads(approved_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        to_email = data.get("to_email") or DEMO_STATE.get("sender_email", "")
        to_name  = data.get("to_name")  or DEMO_STATE.get("sender_name", "")
        subject  = data.get("subject")  or f"Re: {DEMO_STATE.get('subject', 'Your inquiry')}"
        body     = data.get("draft_body") or DEMO_STATE.get("draft_text", "")

    if not to_email:
        err("No recipient email found — skipping send")
        DEMO_STATE["phases_run"].append(9)
        return False

    # Rate limit check
    if _HAS_RATE and not args.dry_run:
        try:
            _limiter.check_and_record("email_send")
        except RateLimitError as e:
            warn(f"Rate limit: {e}")
            DEMO_STATE["phases_run"].append(9)
            return False

    step(f"Sending to: {to_name} <{to_email}>")
    step(f"Subject   : {subject}")

    sent = send_gmail(
        to_email=to_email,
        to_name=to_name,
        subject=subject,
        body=body,
        dry_run=args.dry_run,
    )

    if sent:
        ok("Email sent successfully via Gmail SMTP")
        DEMO_STATE["sent"] = True
    else:
        err("Email send failed — check Gmail credentials")
        DEMO_STATE["sent"] = False

    DEMO_STATE["phases_run"].append(9)
    if _HAS_AUDIT:
        log.info(EV_API_CALL, action="demo_email_sent", to=to_email,
                 dry_run=args.dry_run, success=sent)

    return sent


# ---------------------------------------------------------------------------
# Phase 10: Audit + move to Done
# ---------------------------------------------------------------------------

def phase10_done(args) -> None:
    banner(10, "Audit Log + Move to Done",
           "Files archived, audit trail written, Dashboard updated")

    now = datetime.now(tz=PKT)
    DONE_EMAIL.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Move original task card to Done/email/
    task_file: Optional[Path] = DEMO_STATE.get("task_file")
    if task_file and task_file.exists() and not args.dry_run:
        dest = DONE_EMAIL / task_file.name
        task_file.rename(dest)
        ok(f"Task card → Done/email/{task_file.name}")
    elif task_file:
        ok(f"[DRY RUN] Would move task card → Done/email/")

    # Move approved file to Done if still sitting in Approved/
    approved_path: Optional[Path] = DEMO_STATE.get("approved_path")
    if approved_path and approved_path.exists() and not args.dry_run:
        dest = DONE_EMAIL / f"DONE_{approved_path.stem}.json"
        approved_path.rename(dest)
        ok(f"Approval file → Done/email/{dest.name}")
    elif approved_path:
        ok(f"[DRY RUN] Would move approval file → Done/email/")

    # Write demo completion summary to Logs/
    summary_path = LOGS_DIR / f"DEMO_SUMMARY_{ts_slug()}.json"
    summary = {
        "demo_run_at":   now.isoformat(),
        "phases_run":    DEMO_STATE["phases_run"],
        "sender_email":  DEMO_STATE.get("sender_email"),
        "draft_chars":   len(DEMO_STATE.get("draft_text", "")),
        "email_sent":    DEMO_STATE.get("sent", False),
        "dry_run":       args.dry_run,
        "auto_approve":  args.auto_approve,
        "no_claude":     args.no_claude,
        "no_git":        args.no_git,
        "duration_secs": round(
            (now - datetime.fromisoformat(DEMO_STATE["started_at"])).total_seconds(), 1
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ok(f"Demo summary: {summary_path.relative_to(PROJECT_ROOT)}")
    ok(f"Run log     : {DEMO_LOG.relative_to(PROJECT_ROOT)}")

    # Update Dashboard via DashboardWriter
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        from dashboard_writer import DashboardWriter
        writer = DashboardWriter()
        writer.update_section("Recent Demo Run", [
            f"- Demo completed at {now.strftime('%Y-%m-%d %H:%M PKT')}",
            f"- Phases: {len(DEMO_STATE['phases_run'])}/10",
            f"- Email sent: {'YES' if DEMO_STATE.get('sent') else 'DRY RUN / NO'}",
            f"- Duration: {summary['duration_secs']}s",
        ])
        writer.flush_now()
        ok("Dashboard.md updated (Recent Demo Run section)")
    except Exception:
        pass

    DEMO_STATE["phases_run"].append(10)
    if _HAS_AUDIT:
        log.info(EV_STOP, action="demo_complete",
                 phases=len(DEMO_STATE["phases_run"]),
                 sent=DEMO_STATE.get("sent", False))


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

def print_final_summary(args) -> None:
    width  = 62
    now    = datetime.now(tz=PKT)
    start  = datetime.fromisoformat(DEMO_STATE["started_at"])
    secs   = round((now - start).total_seconds(), 1)

    print()
    print(green("━" * width))
    print(green("  DEMO COMPLETE"))
    print(green("━" * width))

    lines = [
        ("Phases completed", f"{len(DEMO_STATE['phases_run'])}/10"),
        ("Email addressed", DEMO_STATE.get("sender_email", "?")),
        ("Draft generated", f"{len(DEMO_STATE.get('draft_text', ''))} chars"),
        ("Email sent",      green("YES") if DEMO_STATE.get("sent") else yellow("DRY RUN / SKIPPED")),
        ("Duration",        f"{secs}s"),
        ("Mode",            yellow("[DRY RUN]") if args.dry_run else green("[LIVE]")),
    ]
    for label, value in lines:
        print(f"  {bold(label + ':'): <28} {value}")

    print()
    print(dim("  Pipeline: offline → email → cloud draft → approval → send → Done"))
    print(dim(f"  Demo log: vault/Logs/demo_e2e.log"))
    if args.dry_run:
        print()
        print(yellow("  DRY RUN was active — no email was actually sent."))
        print(yellow("  Remove --dry-run (or set DRY_RUN=false) for live mode."))
    print(green("━" * width))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Platinum Tier — end-to-end demo pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/demo_e2e.py                         # interactive demo
  python scripts/demo_e2e.py --auto-approve --dry-run  # CI/CD friendly
  python scripts/demo_e2e.py --no-claude --no-git    # fastest (no API/git)
  python scripts/demo_e2e.py --to test@example.com   # custom recipient
  python scripts/demo_e2e.py --from-phase 4          # resume from phase 4
        """,
    )
    parser.add_argument("--auto-approve",  action="store_true",
                        help="Skip human approval prompt — auto-approve")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Skip real email send and file commits (safe demo mode)")
    parser.add_argument("--no-claude",     action="store_true",
                        help="Use fixture draft instead of calling Claude CLI")
    parser.add_argument("--no-git",        action="store_true",
                        help="Skip git push/pull (simulate sync with print only)")
    parser.add_argument("--no-pause",      action="store_true",
                        help="No pause prompts between phases (fast mode)")
    parser.add_argument("--from-phase",    type=int, default=1, metavar="N",
                        help="Start from phase N (1–10, default 1)")
    parser.add_argument("--to",            metavar="EMAIL",
                        help="Override recipient email address")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Override DRY_RUN from env if flag not set
    if not args.dry_run and os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
        args.dry_run = True

    auto = args.auto_approve or args.no_pause

    # Header
    print()
    print(bold(cyan("╔══════════════════════════════════════════════════════════╗")))
    print(bold(cyan("║  AI Employee — Platinum Tier  │  End-to-End Demo         ║")))
    print(bold(cyan("╚══════════════════════════════════════════════════════════╝")))
    print()
    print(f"  Mode    : {yellow('[DRY RUN]') if args.dry_run else green('[LIVE]')}")
    print(f"  Claude  : {dim('fixture') if args.no_claude else 'API call'}")
    print(f"  Git     : {dim('simulated') if args.no_git else 'real'}")
    print(f"  Approve : {'auto' if args.auto_approve else 'interactive'}")
    print(f"  Start   : {ts_str()} PKT")
    print()

    if args.from_phase == 1:
        pause("start the demo", auto=auto)

    # Prepare email data (use override if --to given)
    email_data = dict(SAMPLE_INCOMING_EMAIL)
    if args.to:
        email_data["from_email"] = args.to
        email_data["from_name"]  = args.to.split("@")[0].replace(".", " ").title()

    # ── Run phases ────────────────────────────────────────────────
    fp = args.from_phase

    if fp <= 1:
        phase1_offline(args)
        pause("see email arriving", auto=auto)

    if fp <= 2:
        card_path = phase2_email_arrives(args, email_data)
        pause("watch Claude draft the reply", auto=auto)
    else:
        # Create a stub card so later phases have a path
        NEEDS_ACTION_EMAIL.mkdir(parents=True, exist_ok=True)
        card_path = NEEDS_ACTION_EMAIL / f"EMAIL_STUB_{ts_slug()}.json"
        card_path.write_text(json.dumps({"stub": True, **email_data}), encoding="utf-8")
        DEMO_STATE.update({
            "task_file":    card_path,
            "sender_email": email_data["from_email"],
            "sender_name":  email_data["from_name"],
            "subject":      email_data["subject"],
        })

    if fp <= 3:
        draft = phase3_cloud_draft(args, card_path, email_data)
        pause("write the approval file", auto=auto)
    else:
        draft = FIXTURE_DRAFT_REPLY
        DEMO_STATE["draft_text"] = draft

    if fp <= 4:
        approval_file = phase4_write_approval(args, card_path, email_data, draft)
        pause("simulate vault sync", auto=auto)
    else:
        # Find the most recent approval file
        PENDING_EMAIL.mkdir(parents=True, exist_ok=True)
        existing = sorted(PENDING_EMAIL.glob("DRAFT_*.json"))
        if existing:
            approval_file = existing[-1]
        else:
            approval_file = phase4_write_approval(args, card_path, email_data, draft)

    if fp <= 5:
        phase5_vault_sync(args)
        pause("bring local machine online", auto=auto)

    if fp <= 6:
        phase6_local_online(args)
        pause("watch approval_watcher claim the file", auto=auto)

    if fp <= 7:
        claimed_path = phase7_approval_watcher(args, approval_file)
        if claimed_path is None:
            err("Approval watcher failed — aborting demo")
            return
        pause("review and approve the draft", auto=auto)
    else:
        claimed_path = approval_file

    if fp <= 8:
        approved = phase8_approve(args, claimed_path)
        if not approved:
            warn("Draft rejected — demo ending at phase 8")
            print_final_summary(args)
            return
        pause("execute the email send", auto=auto)

    if fp <= 9:
        phase9_execute_send(args)
        pause("see the audit log", auto=auto)

    if fp <= 10:
        phase10_done(args)

    print_final_summary(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print(yellow("  Demo interrupted by user."))
        print_final_summary(argparse.Namespace(
            dry_run=True, auto_approve=False, no_claude=True,
            no_git=True, no_pause=True, from_phase=1, to=None
        ))
        sys.exit(0)
