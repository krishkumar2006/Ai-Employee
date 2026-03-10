"""
Audit Logger — Gold Tier
=========================
Central structured JSON logging for the entire AI Employee system.
All watchers, MCPs, and the orchestrator import and use this module.

Log format: JSONL (one JSON object per line)
  → vault/Logs/AUDIT_YYYY-MM-DD.jsonl   (daily rolling file)
  → vault/Logs/HEALTH.json               (live component health snapshot)

Thread-safe: a class-level Lock serialises all writes from any thread
             in the same process. Cross-process safety relies on O_APPEND
             atomicity (safe on both Linux and Windows NTFS for small writes).

Usage:
    from audit_logger import AuditLogger, EV_TASK_CREATED, SEV_WARN

    log = AuditLogger("gmail_watcher")
    log.info(EV_TASK_CREATED, task="EMAIL_foo.md", priority="high")
    log.error(EV_API_FAIL, service="gmail", error="connection timeout")

    # Inline exception helper
    try:
        risky_call()
    except Exception as exc:
        log.exception(EV_API_FAIL, exc, service="odoo")

Part of the Personal AI Employee system.
"""

import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
LOGS_DIR = PROJECT_ROOT / "vault" / "Logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

HEALTH_FILE = LOGS_DIR / "HEALTH.json"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------
SEV_INFO     = "INFO"
SEV_WARN     = "WARN"
SEV_ERROR    = "ERROR"
SEV_CRITICAL = "CRITICAL"

# ---------------------------------------------------------------------------
# Event-type constants
# (import the ones you need — keeps log entries consistent across all files)
# ---------------------------------------------------------------------------

# Lifecycle
EV_START          = "watcher_start"
EV_STOP           = "watcher_stop"
EV_CRASH          = "watcher_crash"
EV_RESTART        = "watcher_restart"

# Task / card processing
EV_TASK_CREATED   = "task_created"
EV_PLAN_GENERATED = "plan_generated"
EV_TASK_ARCHIVED  = "task_archived"
EV_TASK_HANDLED   = "task_handled"

# External API calls
EV_API_CALL       = "api_call"
EV_API_FAIL       = "api_call_failed"
EV_API_RETRY      = "api_retry"
EV_CIRCUIT_OPEN   = "circuit_open"
EV_CIRCUIT_CLOSE  = "circuit_close"

# Communication actions
EV_EMAIL_SENT     = "email_sent"
EV_EMAIL_FAIL     = "email_failed"
EV_POST_PUBLISHED = "post_published"
EV_POST_FAIL      = "post_failed"

# ERP actions
EV_ODOO_ACTION    = "odoo_action"
EV_ODOO_FAIL      = "odoo_failed"

# Offline queue
EV_QUEUE_ENQUEUE  = "queue_enqueued"
EV_QUEUE_DRAIN    = "queue_drained"
EV_QUEUE_EXPIRE   = "queue_expired"
EV_QUEUE_FAIL     = "queue_drain_failed"

# Health / watchdog
EV_HEALTH_CHECK   = "health_check"
EV_ALERT          = "system_alert"


# ---------------------------------------------------------------------------
# AuditLogger class
# ---------------------------------------------------------------------------
class AuditLogger:
    """
    Structured JSON audit logger. One instance per component.

    Every call appends one JSON line to vault/Logs/AUDIT_YYYY-MM-DD.jsonl
    and updates vault/Logs/HEALTH.json with this component's latest status.

    Never raises — logging failures are silently swallowed so they can
    never crash the caller.
    """

    # Shared class-level lock: serialises all writes within one process
    _write_lock = threading.Lock()

    def __init__(self, component: str) -> None:
        """
        Args:
            component: Short identifier used in every log entry.
                       E.g. "gmail_watcher", "odoo_mcp", "orchestrator".
        """
        self.component = component
        self._counters: dict[str, int] = {
            "info": 0, "warn": 0, "error": 0, "critical": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def info(self, event: str, **kwargs: Any) -> None:
        self._write(SEV_INFO, event, **kwargs)

    def warn(self, event: str, **kwargs: Any) -> None:
        self._write(SEV_WARN, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._write(SEV_ERROR, event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._write(SEV_CRITICAL, event, **kwargs)

    def exception(self, event: str, exc: Exception, **kwargs: Any) -> None:
        """Log an exception at ERROR level, automatically including type + message."""
        self._write(
            SEV_ERROR, event,
            error=str(exc),
            error_type=type(exc).__name__,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write(self, severity: str, event: str, **kwargs: Any) -> None:
        try:
            now = datetime.now(tz=PKT)
            entry: dict[str, Any] = {
                "ts":        now.isoformat(),
                "severity":  severity,
                "component": self.component,
                "event":     event,
                **kwargs,
            }

            self._counters[severity.lower()] += 1

            log_file = LOGS_DIR / f"AUDIT_{now.strftime('%Y-%m-%d')}.jsonl"
            line = json.dumps(entry, ensure_ascii=False, default=str)

            with self._write_lock:
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")

            # Best-effort health snapshot (no lock — small atomic write)
            self._update_health(now, severity, event)

        except Exception:
            pass  # Never crash the caller

    def _update_health(self, now: datetime, severity: str, event: str) -> None:
        try:
            if HEALTH_FILE.exists():
                health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            else:
                health = {}

            health[self.component] = {
                "last_seen":    now.isoformat(),
                "last_event":   event,
                "last_severity": severity,
                "errors_total": self._counters["error"] + self._counters["critical"],
            }
            HEALTH_FILE.write_text(json.dumps(health, indent=2), encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level convenience logger — for quick one-off log calls
# ---------------------------------------------------------------------------
_system = AuditLogger("system")

def log_info(event: str, **kwargs: Any)     -> None: _system.info(event, **kwargs)
def log_warn(event: str, **kwargs: Any)     -> None: _system.warn(event, **kwargs)
def log_error(event: str, **kwargs: Any)    -> None: _system.error(event, **kwargs)
def log_critical(event: str, **kwargs: Any) -> None: _system.critical(event, **kwargs)
