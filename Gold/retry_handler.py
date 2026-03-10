"""
Retry Handler — Gold Tier
==========================
Exponential backoff decorator and circuit breaker for all external calls.

Usage — decorator:
    from retry_handler import retry

    @retry(service="odoo")
    def call_odoo():
        return requests.post(...)

    @retry(service="gmail", component="gmail_watcher")
    def fetch_emails(service):
        return service.users().messages().list(...).execute()

Usage — circuit breaker (combine with retry for full protection):
    from retry_handler import CircuitBreaker, CircuitOpenError

    cb = CircuitBreaker.get("odoo")          # singleton per service name

    try:
        with cb:
            result = call_odoo()
    except CircuitOpenError:
        queue_for_later(operation, payload)  # graceful degradation

Usage — get pre-configured retry params for a service:
    cfg = service_retry_config("meta")       # returns dict of retry settings

Part of the Personal AI Employee system.
"""

import functools
import random
import threading
import time
from datetime import timezone, timedelta
from typing import Any, Callable, Optional, Tuple, Type

from audit_logger import AuditLogger, EV_API_RETRY, EV_API_FAIL, EV_CIRCUIT_OPEN, EV_CIRCUIT_CLOSE

PKT = timezone(timedelta(hours=5))
_log = AuditLogger("retry_handler")

# ---------------------------------------------------------------------------
# Per-service retry configuration
# ---------------------------------------------------------------------------
# Tweak these to match each service's SLA and rate-limit behaviour.
RETRY_CONFIGS: dict[str, dict] = {
    # Claude CLI: fast local process — short base delay, cap low
    "claude":     {"max_attempts": 3, "base_delay": 5.0,  "max_delay": 30.0,  "jitter": True},
    # Odoo local JSON-RPC: usually fast but can be slow under load
    "odoo":       {"max_attempts": 5, "base_delay": 2.0,  "max_delay": 120.0, "jitter": True},
    # Gmail API: subject to quota; 4 retries with moderate backoff
    "gmail":      {"max_attempts": 4, "base_delay": 3.0,  "max_delay": 60.0,  "jitter": True},
    # Social APIs: strict rate limits — long base delay
    "meta":       {"max_attempts": 3, "base_delay": 30.0, "max_delay": 300.0, "jitter": True},
    "twitter":    {"max_attempts": 3, "base_delay": 30.0, "max_delay": 300.0, "jitter": True},
    "linkedin":   {"max_attempts": 3, "base_delay": 30.0, "max_delay": 300.0, "jitter": True},
    # Local filesystem: nearly instant, tiny delays
    "filesystem": {"max_attempts": 3, "base_delay": 0.5,  "max_delay": 5.0,   "jitter": False},
    # Fallback for anything not listed
    "default":    {"max_attempts": 3, "base_delay": 2.0,  "max_delay": 60.0,  "jitter": True},
}

# Circuit breaker defaults per service (failures within RECOVERY_TIMEOUT open the circuit)
CIRCUIT_CONFIGS: dict[str, dict] = {
    "odoo":    {"failure_threshold": 5,  "recovery_timeout": 120.0},
    "gmail":   {"failure_threshold": 5,  "recovery_timeout": 60.0},
    "meta":    {"failure_threshold": 3,  "recovery_timeout": 300.0},
    "twitter": {"failure_threshold": 3,  "recovery_timeout": 300.0},
    "claude":  {"failure_threshold": 4,  "recovery_timeout": 60.0},
    "default": {"failure_threshold": 5,  "recovery_timeout": 120.0},
}


def service_retry_config(service: str) -> dict:
    """Return the retry config dict for a given service name."""
    return RETRY_CONFIGS.get(service, RETRY_CONFIGS["default"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_delay(attempt: int, base: float, cap: float, jitter: bool) -> float:
    """Exponential backoff: base * 2^attempt, capped at cap, with optional ±50% jitter."""
    delay = min(base * (2 ** attempt), cap)
    if jitter:
        delay *= 0.5 + random.random()   # [0.5×, 1.5×]
    return max(delay, 0.1)


# ---------------------------------------------------------------------------
# @retry decorator
# ---------------------------------------------------------------------------

def retry(
    max_attempts: int = 3,
    base_delay:   float = 2.0,
    max_delay:    float = 60.0,
    jitter:       bool  = True,
    exceptions:   Tuple[Type[Exception], ...] = (Exception,),
    service:      str = "default",
    component:    str = "unknown",
) -> Callable:
    """
    Retry a function with exponential backoff.

    If `service` is one of the keys in RETRY_CONFIGS the service-level
    values override the keyword arguments, so you only need:

        @retry(service="odoo")
        def my_fn(): ...

    Args:
        max_attempts: Total attempts including the first (1 = no retry).
        base_delay:   Seconds to wait after first failure.
        max_delay:    Maximum wait between attempts.
        jitter:       Randomise delay by ±50% to avoid thundering herd.
        exceptions:   Exception types to catch and retry.
        service:      Key into RETRY_CONFIGS for pre-configured values.
        component:    Name used in audit log entries.
    """
    # Apply service-level overrides
    cfg = RETRY_CONFIGS.get(service, {})
    _max   = cfg.get("max_attempts", max_attempts)
    _base  = cfg.get("base_delay",   base_delay)
    _cap   = cfg.get("max_delay",    max_delay)
    _jit   = cfg.get("jitter",       jitter)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None

            for attempt in range(_max):
                try:
                    return fn(*args, **kwargs)

                except exceptions as exc:
                    last_exc = exc

                    if attempt == _max - 1:
                        # All attempts exhausted — log and re-raise
                        _log.error(EV_API_FAIL,
                            component=component, service=service,
                            function=fn.__name__,
                            attempt=attempt + 1, max_attempts=_max,
                            error=str(exc), error_type=type(exc).__name__,
                        )
                        raise

                    delay = _compute_delay(attempt, _base, _cap, _jit)
                    _log.warn(EV_API_RETRY,
                        component=component, service=service,
                        function=fn.__name__,
                        attempt=attempt + 1, max_attempts=_max,
                        error=str(exc),
                        retry_in_seconds=round(delay, 2),
                    )
                    time.sleep(delay)

            raise last_exc  # satisfies type checkers; unreachable

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitOpenError(Exception):
    """Raised immediately when a circuit is OPEN (service considered down)."""


class CircuitBreaker:
    """
    Classic three-state circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.

    CLOSED    — Normal. Failures increment counter.
    OPEN      — Service down. Calls raise CircuitOpenError immediately.
    HALF_OPEN — Recovery probe: one call allowed through.
                Success → CLOSED, Failure → OPEN (reset timer).

    Use .get() to get the shared singleton for a named service:

        cb = CircuitBreaker.get("odoo")
        try:
            with cb:
                result = call_odoo()
        except CircuitOpenError:
            odoo_queue.enqueue("create_invoice", payload)
        except Exception:
            # real error; circuit has recorded the failure
            raise
    """

    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    # Singleton registry — one CB per service name across the process
    _registry: dict[str, "CircuitBreaker"] = {}
    _registry_lock = threading.Lock()

    @classmethod
    def get(cls, service: str) -> "CircuitBreaker":
        """Return the shared CircuitBreaker for this service (creates if needed)."""
        with cls._registry_lock:
            if service not in cls._registry:
                cfg = CIRCUIT_CONFIGS.get(service, CIRCUIT_CONFIGS["default"])
                cls._registry[service] = cls(
                    service=service,
                    failure_threshold=cfg["failure_threshold"],
                    recovery_timeout=cfg["recovery_timeout"],
                )
            return cls._registry[service]

    def __init__(
        self,
        service:           str,
        failure_threshold: int   = 5,
        recovery_timeout:  float = 120.0,
    ) -> None:
        self.service           = service
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout

        self._state:         str            = self.CLOSED
        self._failure_count: int            = 0
        self._opened_at:     Optional[float]= None
        self._lock                          = threading.Lock()

    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._get_state()

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._get_state() == self.OPEN

    def _get_state(self) -> str:
        """Internal: return current state, auto-transitioning OPEN → HALF_OPEN on timeout."""
        if self._state == self.OPEN:
            if self._opened_at and time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = self.HALF_OPEN
        return self._state

    # ------------------------------------------------------------------

    def __enter__(self) -> "CircuitBreaker":
        with self._lock:
            current = self._get_state()

        if current == self.OPEN:
            raise CircuitOpenError(
                f"Circuit '{self.service}' is OPEN — service is down. "
                f"Retry after {self.recovery_timeout}s."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        with self._lock:
            if exc_type is None:
                # ✓ Success
                if self._state == self.HALF_OPEN:
                    _log.info(EV_CIRCUIT_CLOSE, service=self.service,
                              reason="half_open_probe_succeeded",
                              failures_cleared=self._failure_count)
                self._failure_count = 0
                self._state = self.CLOSED
                self._opened_at = None

            elif exc_type is not CircuitOpenError:
                # ✗ Real failure (not a circuit-open skip)
                self._failure_count += 1
                tripped = (
                    self._failure_count >= self.failure_threshold
                    or self._state == self.HALF_OPEN
                )
                if tripped and self._state != self.OPEN:
                    self._state = self.OPEN
                    self._opened_at = time.monotonic()
                    _log.warn(EV_CIRCUIT_OPEN,
                        service=self.service,
                        failure_count=self._failure_count,
                        threshold=self.failure_threshold,
                        recovery_in_seconds=self.recovery_timeout,
                    )

        return False  # never suppress exceptions

    def reset(self) -> None:
        """Manually close the circuit (e.g. after confirming service is back)."""
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._opened_at = None
        _log.info(EV_CIRCUIT_CLOSE, service=self.service, reason="manual_reset")

    def __repr__(self) -> str:
        return f"<CircuitBreaker service={self.service!r} state={self.state}>"
