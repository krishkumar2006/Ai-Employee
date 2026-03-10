"""
Gmail Watcher — Silver Tier
============================
Polls Gmail via the official API for unread emails, creates task cards
in vault/Needs_Action, and triggers Claude for AI processing.

Prerequisites:
    1. Enable Gmail API in Google Cloud Console
    2. Create OAuth 2.0 credentials (Desktop app)
    3. Download credentials.json into the watchers/ directory
    4. pip install google-api-python-client google-auth-oauthlib

Part of the Personal AI Employee system.
"""

import base64
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

# Gold Tier: structured audit logging + resilience
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from audit_logger import (
    AuditLogger,
    EV_API_CALL,
    EV_API_FAIL,
    EV_CIRCUIT_OPEN,
    EV_PLAN_GENERATED,
    EV_START,
    EV_STOP,
    EV_TASK_CREATED,
)
from retry_handler import CircuitBreaker, CircuitOpenError, retry

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT_PATH: Path = Path(__file__).resolve().parent.parent / "vault"
NEEDS_ACTION_PATH: Path = VAULT_PATH / "Needs_Action"
SKILLS_PATH: Path = VAULT_PATH / "SKILLS.md"
PLANS_PATH: Path = VAULT_PATH / "Plans"

WATCHERS_DIR: Path = Path(__file__).resolve().parent
CREDENTIALS_PATH: Path = WATCHERS_DIR / "credentials.json"
TOKEN_PATH: Path = WATCHERS_DIR / "gmail_token.json"

# Gmail API scope — read-only access to mailbox
SCOPES: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# How often to poll Gmail (seconds)
POLL_INTERVAL: int = 60

# Maximum emails to fetch per poll cycle
MAX_RESULTS: int = 10

# ---------------------------------------------------------------------------
# Logging  (plain logger for console output; AuditLogger for structured audit)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger    = logging.getLogger(__name__)
audit_log = AuditLogger("gmail_watcher")


# ---------------------------------------------------------------------------
# Gmail Authentication
# ---------------------------------------------------------------------------
def authenticate_gmail() -> Resource:
    """Authenticate with Gmail API and return a service object.

    On first run, opens a browser for OAuth consent.
    Subsequent runs reuse the saved token.
    """
    creds: Credentials | None = None

    # Load existing token if available
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # Refresh or create new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Gmail token...")
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                logger.error(
                    "credentials.json not found at %s. "
                    "Download it from Google Cloud Console.",
                    CREDENTIALS_PATH,
                )
                sys.exit(1)

            logger.info("Starting OAuth flow — a browser window will open...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save token for future runs
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        logger.info("Gmail token saved to %s", TOKEN_PATH)

    service = build("gmail", "v1", credentials=creds)
    logger.info("Gmail API authenticated successfully.")
    return service


# ---------------------------------------------------------------------------
# Email Fetching
# ---------------------------------------------------------------------------
@retry(service="gmail", component="gmail_watcher")
def _fetch_message_list(service: Resource) -> list[dict[str, Any]]:
    """Inner call: fetch the list of unread message stubs. Retried on failure."""
    results = (
        service.users()
        .messages()
        .list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=MAX_RESULTS,
        )
        .execute()
    )
    return results.get("messages", [])


@retry(service="gmail", component="gmail_watcher")
def _fetch_message_detail(service: Resource, msg_id: str) -> dict:
    """Inner call: fetch a single email's metadata. Retried on failure."""
    return (
        service.users()
        .messages()
        .get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        )
        .execute()
    )


def get_unread_emails(service: Resource) -> list[dict[str, Any]]:
    """Fetch unread emails from the Gmail inbox (with retry + circuit breaker)."""
    cb = CircuitBreaker.get("gmail")
    try:
        with cb:
            audit_log.info(EV_API_CALL, service="gmail", action="list_messages")
            messages = _fetch_message_list(service)
            return messages
    except CircuitOpenError:
        logger.warning("Gmail circuit is OPEN — skipping poll cycle.")
        audit_log.warn(EV_CIRCUIT_OPEN, service="gmail",
                       reason="circuit_open_skip_poll")
        return []
    except Exception:
        logger.error("Failed to fetch email list", exc_info=True)
        audit_log.error(EV_API_FAIL, service="gmail", action="list_messages")
        return []


def get_email_details(service: Resource, msg_id: str) -> dict[str, str]:
    """Extract subject, sender, date, and snippet from an email (with retry)."""
    cb = CircuitBreaker.get("gmail")
    try:
        with cb:
            msg = _fetch_message_detail(service, msg_id)

        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        snippet = msg.get("snippet", "")
        return {
            "id":        msg_id,
            "subject":   headers.get("Subject", "(no subject)"),
            "sender":    headers.get("From", "(unknown sender)"),
            "date":      headers.get("Date", ""),
            "snippet":   snippet,
            "label_ids": msg.get("labelIds", []),
        }
    except CircuitOpenError:
        logger.warning("Gmail circuit OPEN — skipping message %s", msg_id)
        audit_log.warn(EV_CIRCUIT_OPEN, service="gmail", msg_id=msg_id)
        return {}
    except Exception:
        logger.error("Failed to fetch details for message %s", msg_id, exc_info=True)
        audit_log.error(EV_API_FAIL, service="gmail", action="get_message",
                        msg_id=msg_id)
        return {}


# ---------------------------------------------------------------------------
# Priority Detection
# ---------------------------------------------------------------------------
URGENT_KEYWORDS: set[str] = {
    "urgent", "asap", "immediately", "critical", "deadline",
    "action required", "time sensitive", "important",
}


def detect_priority(subject: str, snippet: str) -> str:
    """Return 'high', 'normal', or 'low' based on keyword detection."""
    combined = f"{subject} {snippet}".lower()
    for keyword in URGENT_KEYWORDS:
        if keyword in combined:
            return "high"
    return "normal"


# ---------------------------------------------------------------------------
# Task Card Creation
# ---------------------------------------------------------------------------
def create_task_card(email: dict[str, str]) -> Path | None:
    """Create a .md task card in Needs_Action for an email."""
    try:
        now = datetime.now(tz=PKT)
        ts_filename = now.strftime("%Y-%m-%dT%H-%M-%S") + now.strftime("%z")[:3]

        # Sanitize subject for filename (keep first 50 chars, replace unsafe chars)
        safe_subject = (
            email["subject"][:50]
            .replace("/", "-")
            .replace("\\", "-")
            .replace(":", "-")
            .replace("*", "")
            .replace("?", "")
            .replace('"', "")
            .replace("<", "")
            .replace(">", "")
            .replace("|", "-")
            .strip()
        )

        task_filename = f"EMAIL_{safe_subject}_{ts_filename}.md"
        task_path = NEEDS_ACTION_PATH / task_filename

        priority = detect_priority(email["subject"], email["snippet"])

        # Parse the email date into PKT
        email_date_str = email.get("date", "")
        try:
            email_dt = parsedate_to_datetime(email_date_str).astimezone(PKT)
            email_iso = email_dt.isoformat()
            email_human = email_dt.strftime("%Y-%m-%d %H:%M:%S") + " PKT"
        except Exception:
            email_iso = now.isoformat()
            email_human = now.strftime("%Y-%m-%d %H:%M:%S") + " PKT"

        content = (
            f"---\n"
            f"type: email\n"
            f"source: gmail\n"
            f"gmail_id: {email['id']}\n"
            f"subject: {email['subject']}\n"
            f"sender: {email['sender']}\n"
            f"received_at: {email_iso}\n"
            f"created_at: {now.isoformat()}\n"
            f"status: pending\n"
            f"priority: {priority}\n"
            f"---\n"
            f"\n"
            f"New email detected in Gmail inbox.\n"
            f"\n"
            f"From: {email['sender']}\n"
            f"Subject: {email['subject']}\n"
            f"Received: {email_human}\n"
            f"\n"
            f"Preview:\n"
            f"> {email['snippet']}\n"
            f"\n"
            f"Next possible actions (AI Employee will decide):\n"
            f"- Classify: urgent / reply_needed / informational / spam\n"
            f"- Draft a reply\n"
            f"- Extract action items\n"
            f"- Archive if no action needed\n"
            f"- Flag for human review\n"
        )

        task_path.write_text(content, encoding="utf-8")
        logger.info("Task card created: %s [priority=%s]", task_filename, priority)
        audit_log.info(
            EV_TASK_CREATED,
            task=task_filename,
            source="gmail",
            gmail_id=email.get("id"),
            subject=email.get("subject", "")[:120],
            sender=email.get("sender", "")[:120],
            priority=priority,
        )
        return task_path

    except Exception as exc:
        logger.error("Failed to create task card for email: %s",
                      email.get("subject", "unknown"), exc_info=True)
        audit_log.exception(EV_API_FAIL, exc,
                            action="create_task_card",
                            subject=email.get("subject", "")[:120])
        return None


# ---------------------------------------------------------------------------
# Claude Trigger
# ---------------------------------------------------------------------------
def trigger_claude(task_path: Path) -> None:
    """Generate a Plan.md from the task card using Claude CLI."""
    plan_filename = task_path.stem + "_PLAN.md"
    plan_path = PLANS_PATH / plan_filename

    prompt = (
        f"You are the AI Employee. A new task card exists at: {task_path}\n"
        f"Read it and the skills file at: {SKILLS_PATH}\n\n"
        f"Generate a structured plan. Output ONLY the plan markdown, nothing else.\n"
        f"Use YAML frontmatter with: source_task, created_at, status, priority.\n"
        f"Then sections: Summary, Recommended Actions (numbered), "
        f"Deadline Estimate, Notes."
    )

    try:
        logger.info("Triggering Claude to generate plan for: %s", task_path.name)
        result = subprocess.run(
            ["claude", "--print", "--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(VAULT_PATH),
        )

        if result.returncode == 0:
            plan_path.write_text(result.stdout, encoding="utf-8")
            logger.info("Plan created: %s", plan_path.name)
            audit_log.info(
                EV_PLAN_GENERATED,
                task=task_path.name,
                plan=plan_path.name,
            )
        else:
            logger.error("Claude returned exit code %d: %s",
                         result.returncode, result.stderr[:500])
            audit_log.error(
                EV_API_FAIL,
                service="claude",
                task=task_path.name,
                exit_code=result.returncode,
                error=result.stderr[:300],
            )

    except FileNotFoundError:
        logger.warning("Claude CLI not found — task card saved, no plan generated.")
        audit_log.warn(EV_API_FAIL, service="claude", reason="cli_not_found",
                       task=task_path.name)
    except subprocess.TimeoutExpired:
        logger.error("Claude timed out processing %s", task_path.name)
        audit_log.error(EV_API_FAIL, service="claude", reason="timeout",
                        task=task_path.name)
    except Exception as exc:
        logger.error("Failed to trigger Claude", exc_info=True)
        audit_log.exception(EV_API_FAIL, exc, service="claude", task=task_path.name)


# ---------------------------------------------------------------------------
# State Tracking (avoid reprocessing)
# ---------------------------------------------------------------------------
def load_processed_ids() -> set[str]:
    """Load the set of already-processed Gmail message IDs from disk."""
    state_file = WATCHERS_DIR / ".gmail_processed_ids"
    if state_file.exists():
        return set(state_file.read_text(encoding="utf-8").splitlines())
    return set()


def save_processed_id(msg_id: str) -> None:
    """Append a processed message ID to the state file."""
    state_file = WATCHERS_DIR / ".gmail_processed_ids"
    with state_file.open("a", encoding="utf-8") as f:
        f.write(msg_id + "\n")


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
def main() -> None:
    # Ensure directories exist
    NEEDS_ACTION_PATH.mkdir(parents=True, exist_ok=True)
    PLANS_PATH.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 55)
    print("  AI Employee — Gmail Watcher (Silver Tier)")
    print("=" * 55)
    print(f"  Output   : {NEEDS_ACTION_PATH}")
    print(f"  Poll     : every {POLL_INTERVAL}s")
    print("  Press Ctrl+C to stop")
    print("=" * 55)
    print()

    # Authenticate (may open browser on first run)
    service = authenticate_gmail()
    processed_ids = load_processed_ids()

    logger.info("Loaded %d previously processed email IDs.", len(processed_ids))
    logger.info("Gmail watcher started. Polling every %ds...", POLL_INTERVAL)
    audit_log.info(EV_START, poll_interval=POLL_INTERVAL,
                   processed_ids_loaded=len(processed_ids))

    try:
        while True:
            messages = get_unread_emails(service)

            if not messages:
                logger.info("No new unread emails.")
            else:
                logger.info("Found %d unread email(s).", len(messages))

            for msg_ref in messages:
                msg_id = msg_ref["id"]

                # Skip already-processed emails
                if msg_id in processed_ids:
                    continue

                email = get_email_details(service, msg_id)
                if not email:
                    continue

                logger.info("Processing: [%s] from %s",
                            email["subject"], email["sender"])

                task_path = create_task_card(email)
                if task_path:
                    trigger_claude(task_path)

                # Mark as processed regardless of Claude success
                processed_ids.add(msg_id)
                save_processed_id(msg_id)

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print()
        logger.info("Shutting down Gmail watcher...")
        audit_log.info(EV_STOP, reason="keyboard_interrupt")

    print("Gmail watcher stopped. Goodbye!")


if __name__ == "__main__":
    main()
