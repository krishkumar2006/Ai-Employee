"""
demo_send_email.py — Platinum Tier
====================================
Standalone Gmail SMTP sender for approved draft files.

Reads a JSON draft from vault/Approved/email/<filename>.json,
sends it via Gmail SMTP SSL (free — no API subscription), writes
an audit entry, and moves the file to vault/Done/email/.

Usage
-----
  python scripts/demo_send_email.py                     # send first pending
  python scripts/demo_send_email.py DRAFT_EMAIL_*.json  # send specific file
  python scripts/demo_send_email.py --list              # list approved drafts
  python scripts/demo_send_email.py --dry-run           # show without sending

Requirements
------------
  GMAIL_ADDRESS      — your Gmail address (set in .env.local)
  GMAIL_APP_PASSWORD — 16-char Google App Password (set in .env.local)
  No extra pip packages — uses stdlib smtplib only.
"""

import argparse
import json
import smtplib
import sys
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from config import cfg
    _HAS_CFG = True
except ImportError:
    cfg = None
    _HAS_CFG = False

try:
    from audit_logger import AuditLogger, EV_API_CALL, EV_API_FAIL, EV_ALERT
    _log = AuditLogger("demo_send_email")
    _HAS_LOG = True
except ImportError:
    _log = None
    _HAS_LOG = False

try:
    from rate_limiter import limiter, RateLimitError
    _HAS_RATE = True
except ImportError:
    _HAS_RATE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT          = PROJECT_ROOT / "vault"
APPROVED_EMAIL = VAULT / "Approved" / "email"
DONE_EMAIL     = VAULT / "Done"    / "email"
LOGS_DIR       = VAULT / "Logs"

PKT = timezone(timedelta(hours=5))


def _get(key: str, default: str = "") -> str:
    if _HAS_CFG:
        return cfg.get(key, default)
    import os
    return os.environ.get(key, default)


def _is_dry_run() -> bool:
    if _HAS_CFG:
        return cfg.is_dry_run()
    import os
    return os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------

def send_email(to_email: str, to_name: str, subject: str, body: str,
               dry_run: bool = False) -> bool:
    """Send email via Gmail SMTP SSL. Returns True on success."""
    gmail_addr = _get("GMAIL_ADDRESS")
    app_pass   = _get("GMAIL_APP_PASSWORD")

    if dry_run:
        print(f"[DRY RUN] Would send:")
        print(f"  From   : {gmail_addr or '(GMAIL_ADDRESS not set)'}")
        print(f"  To     : {to_name} <{to_email}>")
        print(f"  Subject: {subject}")
        print(f"  Length : {len(body)} chars")
        return True

    if not gmail_addr or not app_pass:
        print("ERROR: GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set in .env.local")
        print("  Google App Password: Account → Security → 2-Step → App passwords")
        return False

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"AI Employee <{gmail_addr}>"
    msg["To"]      = f"{to_name} <{to_email}>"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.ehlo()
            server.login(gmail_addr, app_pass)
            server.sendmail(gmail_addr, [to_email], msg.as_string())
        print(f"  Sent to: {to_name} <{to_email}>  ✓")
        return True

    except smtplib.SMTPAuthenticationError:
        print("ERROR: Gmail authentication failed.")
        print("  → Check GMAIL_APP_PASSWORD is a 16-char Google App Password")
        print("  → Not your regular Gmail password")
        if _HAS_LOG:
            _log.error(EV_API_FAIL, service="gmail_smtp",
                       reason="auth_failed", to=to_email)
        return False

    except smtplib.SMTPRecipientsRefused:
        print(f"ERROR: Recipient refused by Gmail: {to_email}")
        return False

    except smtplib.SMTPException as e:
        print(f"SMTP error: {e}")
        if _HAS_LOG:
            _log.error(EV_API_FAIL, service="gmail_smtp", error=str(e))
        return False

    except Exception as e:
        print(f"Send error: {e}")
        return False


# ---------------------------------------------------------------------------
# Process one approved draft file
# ---------------------------------------------------------------------------

def process_draft(path: Path, dry_run: bool = False) -> bool:
    """Read, send, and archive one approved draft. Returns True on success."""
    print(f"\n[demo_send_email] Processing: {path.name}")

    # Parse the draft
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: Cannot parse {path.name}: {e}")
        return False

    to_email = data.get("to_email", "")
    to_name  = data.get("to_name", "")
    subject  = data.get("subject", "(no subject)")
    body     = data.get("draft_body", "")

    if not to_email or not body:
        print(f"ERROR: Missing to_email or draft_body in {path.name}")
        return False

    # Rate limit
    if _HAS_RATE and not dry_run:
        try:
            limiter.check_and_record("email_send")
        except RateLimitError as e:
            print(f"RATE LIMIT: {e}")
            return False

    # Send
    sent = send_email(to_email, to_name, subject, body, dry_run=dry_run)

    if sent:
        # Move to Done
        DONE_EMAIL.mkdir(parents=True, exist_ok=True)
        done_path = DONE_EMAIL / f"SENT_{path.stem}.json"
        result_data = {
            **data,
            "status":  "sent" if not dry_run else "dry_run",
            "sent_at": datetime.now(tz=PKT).isoformat(),
        }
        if not dry_run:
            done_path.write_text(json.dumps(result_data, indent=2), encoding="utf-8")
            path.unlink()
            print(f"  Moved  → Done/email/{done_path.name}")
        else:
            print(f"  [DRY RUN] Would move → Done/email/{done_path.name}")

        if _HAS_LOG:
            _log.info(EV_API_CALL, action="email_sent", to=to_email,
                      subject=subject, dry_run=dry_run)
    return sent


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send approved email drafts via Gmail SMTP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/demo_send_email.py                     # send first pending
  python scripts/demo_send_email.py DRAFT_EMAIL_*.json  # specific file
  python scripts/demo_send_email.py --list              # list pending
  python scripts/demo_send_email.py --all --dry-run     # test all, no send
  DRY_RUN=true python scripts/demo_send_email.py        # env var dry-run
        """,
    )
    parser.add_argument("file",     nargs="?",
                        help="Draft filename in vault/Approved/email/ (default: first found)")
    parser.add_argument("--list",   action="store_true",
                        help="List all pending approved drafts and exit")
    parser.add_argument("--all",    action="store_true",
                        help="Process all approved drafts (not just first)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be sent without actually sending")
    args = parser.parse_args()

    dry_run = args.dry_run or _is_dry_run()

    # Ensure directories exist
    APPROVED_EMAIL.mkdir(parents=True, exist_ok=True)
    DONE_EMAIL.mkdir(parents=True, exist_ok=True)

    # List mode
    if args.list:
        files = sorted(APPROVED_EMAIL.glob("*.json"))
        if not files:
            print(f"No approved email drafts in {APPROVED_EMAIL.relative_to(PROJECT_ROOT)}")
            return
        print(f"Approved email drafts ({len(files)}):\n")
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                to    = d.get("to_email", "?")
                subj  = d.get("subject", "?")[:50]
                pri   = d.get("priority", "?")
                print(f"  {f.name}")
                print(f"    To: {to}  |  {subj}  |  priority={pri}")
            except Exception:
                print(f"  {f.name}  (unreadable)")
        return

    # Resolve file(s) to process
    if args.file:
        target = APPROVED_EMAIL / args.file
        if not target.exists():
            target = Path(args.file)
        if not target.exists():
            print(f"ERROR: File not found: {args.file}")
            sys.exit(1)
        targets = [target]
    elif args.all:
        targets = sorted(APPROVED_EMAIL.glob("*.json"))
        if not targets:
            print("No approved drafts to send.")
            sys.exit(0)
    else:
        all_drafts = sorted(APPROVED_EMAIL.glob("*.json"))
        if not all_drafts:
            print(f"No approved drafts found in {APPROVED_EMAIL.relative_to(PROJECT_ROOT)}")
            print("  Run: python scripts/demo_e2e.py --auto-approve")
            sys.exit(0)
        targets = [all_drafts[0]]

    print(f"[demo_send_email] mode={'DRY RUN' if dry_run else 'LIVE'}")
    print(f"  Files to process: {len(targets)}")
    if dry_run:
        print("  (DRY RUN — no email will actually be sent)\n")

    success = 0
    for t in targets:
        ok = process_draft(t, dry_run=dry_run)
        if ok:
            success += 1

    print(f"\nDone: {success}/{len(targets)} sent successfully.")
    sys.exit(0 if success == len(targets) else 1)


if __name__ == "__main__":
    main()
