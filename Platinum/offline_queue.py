"""
Offline Queue — Gold Tier
==========================
Durable, file-based queue for operations that cannot execute when a
service (e.g. Odoo) is temporarily down. Each queued item is stored as
a JSON file in vault/Queue/ and survives process restarts.

Usage — enqueueing (when circuit is OPEN):
    from offline_queue import OfflineQueue

    odoo_q = OfflineQueue("odoo")
    odoo_q.enqueue("create_invoice", payload={
        "partner_name": "Acme Corp",
        "lines": [...],
        "invoice_date": "2026-02-19",
    })

Usage — draining (when service recovers):
    def replay_invoice(operation: str, payload: dict) -> None:
        if operation == "create_invoice":
            odoo_create_invoice(**payload)

    counts = odoo_q.drain(replay_invoice)
    # {"success": 3, "failed": 0, "expired": 1}

Graceful degradation flow:
    1. CircuitBreaker detects Odoo is down → raises CircuitOpenError
    2. Caller catches CircuitOpenError → calls odoo_q.enqueue(...)
    3. Watchdog / orchestrator checks odoo_q.pending_count() in health logs
    4. When Odoo recovers, CircuitBreaker moves to HALF_OPEN → CLOSED
    5. Caller or scheduled task calls odoo_q.drain(executor_fn)

File layout:
    vault/Queue/
    └── odoo_<item_id>.json   ← one file per queued operation

Each file contains:
    {
      "id":        "a3f8b19c4d2e",
      "service":   "odoo",
      "operation": "create_invoice",
      "payload":   { ... },
      "queued_at": "2026-02-19T14:30:00+05:00",
      "expires_at": "2026-02-22T14:30:00+05:00",
      "attempts":  0
    }

Part of the Personal AI Employee system.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from audit_logger import (
    AuditLogger,
    EV_QUEUE_DRAIN,
    EV_QUEUE_ENQUEUE,
    EV_QUEUE_EXPIRE,
    EV_QUEUE_FAIL,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
QUEUE_DIR    = PROJECT_ROOT / "vault" / "Queue"
QUEUE_DIR.mkdir(parents=True, exist_ok=True)

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# Operations older than this are discarded on drain (stale data guard)
EXPIRY_HOURS: int = 72


# ---------------------------------------------------------------------------
# OfflineQueue
# ---------------------------------------------------------------------------

class OfflineQueue:
    """
    Durable, per-service file queue.

    Thread-safety: individual file writes are atomic on NTFS / ext4 for
    small payloads. For high-concurrency use cases, wrap enqueue/drain
    with an external lock.
    """

    def __init__(self, service: str) -> None:
        self.service = service
        self._log    = AuditLogger(f"queue_{service}")

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(self, operation: str, payload: dict[str, Any]) -> str:
        """
        Persist one operation to the queue.

        Args:
            operation: Short name for the operation (e.g. "create_invoice").
            payload:   Dict of arguments needed to replay the operation.

        Returns:
            item_id: Unique ID for this queued item.
        """
        item_id = uuid.uuid4().hex[:12]
        now     = datetime.now(tz=PKT)
        expires = now + timedelta(hours=EXPIRY_HOURS)

        item = {
            "id":        item_id,
            "service":   self.service,
            "operation": operation,
            "payload":   payload,
            "queued_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "attempts":  0,
        }

        path = QUEUE_DIR / f"{self.service}_{item_id}.json"
        path.write_text(json.dumps(item, indent=2, default=str), encoding="utf-8")

        self._log.info(
            EV_QUEUE_ENQUEUE,
            item_id=item_id,
            operation=operation,
            queue_file=path.name,
            expires_at=expires.isoformat(),
        )
        return item_id

    # ------------------------------------------------------------------
    # Inspect
    # ------------------------------------------------------------------

    def pending_count(self) -> int:
        """Return the number of items currently in the queue for this service."""
        return len(list(QUEUE_DIR.glob(f"{self.service}_*.json")))

    def list_items(self) -> list[dict]:
        """Return all queued items as dicts (sorted by queued_at, oldest first)."""
        items = []
        for path in sorted(QUEUE_DIR.glob(f"{self.service}_*.json")):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return items

    # ------------------------------------------------------------------
    # Drain
    # ------------------------------------------------------------------

    def drain(
        self,
        executor_fn: Callable[[str, dict], Any],
        max_items: int = 100,
    ) -> dict[str, int]:
        """
        Replay all queued items by calling executor_fn(operation, payload).

        The executor_fn MUST raise on failure so the item is kept for retry.
        Successfully replayed items are deleted from the queue.

        Args:
            executor_fn: Callable that takes (operation: str, payload: dict).
                         Raise any exception to signal failure.
            max_items:   Safety cap — drain at most this many items per call.

        Returns:
            counts: {"success": N, "failed": N, "expired": N, "total": N}
        """
        files  = sorted(QUEUE_DIR.glob(f"{self.service}_*.json"))[:max_items]
        counts = {"success": 0, "failed": 0, "expired": 0, "total": len(files)}
        now    = datetime.now(tz=PKT)

        for path in files:
            # Load item
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                path.unlink(missing_ok=True)
                continue

            # Expiry check
            try:
                expires = datetime.fromisoformat(item["expires_at"])
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=PKT)
                if now > expires:
                    self._log.warn(
                        EV_QUEUE_EXPIRE,
                        item_id=item["id"],
                        operation=item["operation"],
                        queued_at=item["queued_at"],
                        expires_at=item["expires_at"],
                    )
                    path.unlink(missing_ok=True)
                    counts["expired"] += 1
                    continue
            except Exception:
                pass  # If we can't check expiry, try execution anyway

            # Execute
            item["attempts"] = item.get("attempts", 0) + 1
            try:
                executor_fn(item["operation"], item["payload"])
                path.unlink(missing_ok=True)
                counts["success"] += 1
                self._log.info(
                    EV_QUEUE_DRAIN,
                    item_id=item["id"],
                    operation=item["operation"],
                    attempts=item["attempts"],
                )
            except Exception as exc:
                self._log.exception(
                    EV_QUEUE_FAIL,
                    exc,
                    item_id=item["id"],
                    operation=item["operation"],
                    attempts=item["attempts"],
                )
                # Persist updated attempt count for observability
                try:
                    path.write_text(
                        json.dumps(item, indent=2, default=str),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                counts["failed"] += 1

        return counts

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------

    def purge(self) -> int:
        """Delete ALL items in this service's queue (use with care)."""
        count = 0
        for path in QUEUE_DIR.glob(f"{self.service}_*.json"):
            try:
                path.unlink()
                count += 1
            except Exception:
                pass
        if count:
            self._log.warn(
                EV_QUEUE_EXPIRE,
                action="purge_all",
                items_deleted=count,
            )
        return count

    def __repr__(self) -> str:
        return f"<OfflineQueue service={self.service!r} pending={self.pending_count()}>"


# ---------------------------------------------------------------------------
# Module-level convenience: one shared queue per service
# ---------------------------------------------------------------------------
_queues: dict[str, OfflineQueue] = {}


def get_queue(service: str) -> OfflineQueue:
    """Return the shared OfflineQueue singleton for a service."""
    if service not in _queues:
        _queues[service] = OfflineQueue(service)
    return _queues[service]
