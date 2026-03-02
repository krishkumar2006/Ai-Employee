"""
update_publisher.py — Platinum Tier
=====================================
Cloud-safe helper that writes structured JSON event files to
vault/Updates/. Called by cloud components (social_drafter,
gmail_watcher, ralph_loop) when they complete a task so the
local machine's update_merger.py can reflect the status in
Dashboard.md after Git sync.

Update file format:
  vault/Updates/<UTC_TIMESTAMP>_<component>_<event>.json

  {
    "timestamp_utc": "2026-03-02T09:00:00+00:00",
    "component":     "social_drafter",
    "event":         "draft_created",
    "domain":        "social",
    "summary":       "Twitter + LinkedIn draft ready for ACME campaign",
    "data":          { ... event-specific payload ... }
  }

Usage as a library:
    from watchers.update_publisher import publish_update

    publish_update(
        component="social_drafter",
        event="draft_created",
        domain="social",
        summary="Twitter draft ready: 'AI trends in 2026'",
        data={"draft_file": "DRAFT_social_20260302T090000.md"},
    )

CLI usage:
    python watchers/update_publisher.py \\
        --component social_drafter \\
        --event draft_created \\
        --domain social \\
        --summary "Twitter draft ready"
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from audit_logger import AuditLogger, EV_TASK_CREATED
    _log: object = AuditLogger("update_publisher")
except ImportError:
    _log = None

VAULT       = PROJECT_ROOT / "vault"
UPDATES_DIR = VAULT / "Updates"

# Maximum filename length for component/event parts (chars, excl. timestamp)
_MAX_SLUG = 32


def publish_update(
    component: str,
    event:     str,
    domain:    str,
    summary:   str,
    data:      dict | None = None,
) -> Path:
    """
    Write one structured update event to vault/Updates/.

    Args:
        component: Originating component name  (e.g. "social_drafter")
        event:     What happened                (e.g. "draft_created")
        domain:    Task domain                  (e.g. "social", "email")
        summary:   One-line human-readable desc (shown in Dashboard)
        data:      Optional event-specific payload dict

    Returns:
        Path of the written JSON file.
    """
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)

    now    = datetime.now(tz=timezone.utc)
    ts_str = now.strftime("%Y%m%dT%H%M%SZ")

    # Build a filesystem-safe slug from component + event
    def _slug(s: str) -> str:
        return s.replace("/", "-").replace(" ", "_")[:_MAX_SLUG]

    filename = f"{ts_str}_{_slug(component)}_{_slug(event)}.json"

    payload = {
        "timestamp_utc": now.isoformat(),
        "component":     component,
        "event":         event,
        "domain":        domain,
        "summary":       summary,
        "data":          data or {},
    }

    # Dry-run: describe what would happen without writing
    import os as _os
    _dry = _os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    if _dry:
        print(f"[update_publisher] [DRY RUN] Would write: {filename}")
        print(f"  summary: {summary}")
        return UPDATES_DIR / filename  # return path without creating

    path = UPDATES_DIR / filename
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if _log is not None:
        _log.info(  # type: ignore[union-attr]
            EV_TASK_CREATED,
            action="update_published",
            component=component,
            source_event=event,
            domain=domain,
            file=filename,
        )

    print(f"[update_publisher] Published: {filename}")
    return path


# ---------------------------------------------------------------------------
# Entry point (CLI)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Publish a structured update event to vault/Updates/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python watchers/update_publisher.py \\
      --component social_drafter --event draft_created \\
      --domain social --summary "Twitter draft ready"

  python watchers/update_publisher.py \\
      --component gmail_watcher --event triage_complete \\
      --domain email --summary "12 emails triaged, 3 actionable" \\
      --data '{"actionable": 3, "archived": 9}'
        """,
    )
    parser.add_argument("--component", required=True,
                        help="Component name (e.g. social_drafter)")
    parser.add_argument("--event",     required=True,
                        help="Event type (e.g. draft_created, triage_complete)")
    parser.add_argument("--domain",    required=True,
                        help="Domain: email | odoo | social | calendar | general")
    parser.add_argument("--summary",   required=True,
                        help="One-line human-readable description")
    parser.add_argument("--data",      default="{}",
                        help="Optional JSON payload string (default: {})")
    args = parser.parse_args()

    try:
        extra = json.loads(args.data)
    except json.JSONDecodeError:
        extra = {"raw": args.data}

    out = publish_update(
        component=args.component,
        event=args.event,
        domain=args.domain,
        summary=args.summary,
        data=extra,
    )
    print(f"Written to: {out}")
