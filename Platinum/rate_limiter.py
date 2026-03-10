"""
rate_limiter.py — Platinum Tier
================================
Sliding-window rate limiter for the AI Employee system.
Caps destructive / external actions to prevent runaway automation.

Design: file-backed JSON state (vault/Logs/.rate_limits.json)
  - No Redis, no paid services — 100 % free
  - Thread-safe via in-process threading.Lock
  - Multi-process safe via atomic read-modify-write + tmp-replace

Configurable limits via env vars (set in .env.cloud / .env.local):
  RATE_LIMIT_ODOO_WRITE=10       max Odoo write actions / hour
  RATE_LIMIT_EMAIL_SEND=20       max outbound emails / hour
  RATE_LIMIT_SOCIAL_POST=5       max social posts / hour
  RATE_LIMIT_SOCIAL_DRAFT=20     max Claude draft calls / hour
  RATE_LIMIT_CLAUDE_CALL=50      max Claude CLI invocations / hour
  RATE_LIMIT_APPROVAL=30         max approval decisions / hour
  RATE_LIMIT_DEFAULT=10          fallback for unlisted actions

Usage
-----
    from rate_limiter import RateLimiter, RateLimitError

    limiter = RateLimiter()

    # Check + record atomically (raises RateLimitError if over limit)
    try:
        remaining = limiter.check_and_record("odoo_write")
        # ... do the write ...
    except RateLimitError as e:
        print(f"Blocked: {e}")

    # Read-only check (for dry-run / status display)
    remaining = limiter.remaining("email_send")

    # CLI status report
    python rate_limiter.py [--action ACTION] [--reset ACTION]
"""

import json
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
VAULT        = PROJECT_ROOT / "vault"
LOGS_DIR     = VAULT / "Logs"
STATE_FILE   = LOGS_DIR / ".rate_limits.json"   # hidden: never synced to Git

PKT          = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Default limits (actions / hour)
# ---------------------------------------------------------------------------
DEFAULT_LIMITS: dict[str, int] = {
    "odoo_write":    10,   # invoice/payment confirms
    "odoo_read":    100,   # read-only Odoo calls
    "email_send":    20,   # outbound SMTP
    "email_read":   100,   # inbox polling (high limit)
    "social_post":    5,   # Playwright posts per platform
    "social_draft":  20,   # Claude draft generations
    "claude_call":   50,   # any Claude CLI invocation
    "approval":      30,   # approval decisions
    "vault_write":  100,   # file writes to vault
    "health_check": 500,   # health checks (very high)
}
DEFAULT_MAX   = 10
WINDOW_SECS   = 3600      # 1-hour sliding window

# Module-level lock — shared across all RateLimiter instances in one process
_global_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class RateLimitError(Exception):
    """Raised when check_and_record() finds the limit has been reached."""

    def __init__(self, action: str, used: int, max_per_hour: int, reset_in_secs: int):
        self.action       = action
        self.used         = used
        self.max_per_hour = max_per_hour
        self.reset_in     = reset_in_secs
        minutes, secs = divmod(max(reset_in_secs, 0), 60)
        super().__init__(
            f"Rate limit exceeded for '{action}': "
            f"{used}/{max_per_hour} per hour used. "
            f"Resets in {minutes}m {secs}s."
        )


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Sliding-window rate limiter backed by a JSON file.

    Thread-safe; multi-process-safe via atomic tmp.replace().
    Each instance shares _global_lock so concurrent imports
    in the same process serialize correctly.
    """

    def __init__(self) -> None:
        self._limits = self._load_config_limits()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_and_record(
        self,
        action: str,
        max_per_hour: Optional[int] = None,
    ) -> int:
        """
        Check if action is within its rate limit, then record it.

        Returns remaining count (after recording this action).
        Raises RateLimitError if the limit is already reached.
        """
        max_count = max_per_hour if max_per_hour is not None else self._limits.get(action, DEFAULT_MAX)

        with _global_lock:
            state = self._read()
            now   = time.time()
            ts    = self._window(state, action, now)

            if len(ts) >= max_count:
                oldest   = min(ts)
                reset_in = int(WINDOW_SECS - (now - oldest)) + 1
                raise RateLimitError(action, len(ts), max_count, reset_in)

            ts.append(now)
            self._update(state, action, ts, max_count)
            self._write(state)
            return max_count - len(ts)

    def remaining(
        self,
        action: str,
        max_per_hour: Optional[int] = None,
    ) -> int:
        """Return remaining allowed calls this hour (read-only — no recording)."""
        max_count = max_per_hour if max_per_hour is not None else self._limits.get(action, DEFAULT_MAX)

        with _global_lock:
            state = self._read()
            now   = time.time()
            ts    = self._window(state, action, now)
            return max(0, max_count - len(ts))

    def record(self, action: str, max_per_hour: Optional[int] = None) -> None:
        """Record an action without checking the limit (use only after an explicit check)."""
        max_count = max_per_hour if max_per_hour is not None else self._limits.get(action, DEFAULT_MAX)

        with _global_lock:
            state = self._read()
            now   = time.time()
            ts    = self._window(state, action, now)
            ts.append(now)
            self._update(state, action, ts, max_count)
            self._write(state)

    def reset(self, action: str) -> None:
        """Clear all recorded timestamps for action (admin use only)."""
        with _global_lock:
            state = self._read()
            if action in state:
                state[action]["timestamps"] = []
                self._write(state)
                print(f"[rate_limiter] Reset counter for '{action}'")
            else:
                print(f"[rate_limiter] No counter found for '{action}'")

    def status(self) -> dict[str, dict]:
        """Return current usage summary for all tracked actions."""
        with _global_lock:
            state = self._read()
            now   = time.time()
            result: dict[str, dict] = {}
            for action, data in state.items():
                ts        = self._window(state, action, now)
                max_count = data.get("max_per_hour", self._limits.get(action, DEFAULT_MAX))
                result[action] = {
                    "used":         len(ts),
                    "max_per_hour": max_count,
                    "remaining":    max(0, max_count - len(ts)),
                    "pct_used":     round(len(ts) / max_count * 100) if max_count else 0,
                }
            return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> dict:
        """Read state file (or return empty dict on missing/corrupt)."""
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _write(self, state: dict) -> None:
        """Atomically write state file via tmp-replace."""
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)

    @staticmethod
    def _window(state: dict, action: str, now: float) -> list[float]:
        """Return timestamps for action that fall within the current window."""
        cutoff = now - WINDOW_SECS
        return [t for t in state.get(action, {}).get("timestamps", []) if t > cutoff]

    @staticmethod
    def _update(state: dict, action: str, ts: list[float], max_per_hour: int) -> None:
        """Write updated timestamps back into the state dict (in-place)."""
        if action not in state:
            state[action] = {}
        state[action]["timestamps"]   = ts
        state[action]["last_updated"] = time.time()
        state[action]["max_per_hour"] = max_per_hour

    def _load_config_limits(self) -> dict[str, int]:
        """Read RATE_LIMIT_* env vars and merge over defaults."""
        import os
        limits = dict(DEFAULT_LIMITS)
        for action in list(DEFAULT_LIMITS.keys()):
            env_key = f"RATE_LIMIT_{action.upper()}"
            val     = os.environ.get(env_key, "").strip()
            if val.isdigit():
                limits[action] = int(val)
        return limits


# ---------------------------------------------------------------------------
# Module-level singleton (import this in scripts)
# ---------------------------------------------------------------------------
limiter = RateLimiter()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rate limiter status / admin")
    parser.add_argument("--status", action="store_true", default=True,
                        help="Show current usage for all actions (default)")
    parser.add_argument("--action", metavar="ACTION",
                        help="Show usage for a specific action")
    parser.add_argument("--reset",  metavar="ACTION",
                        help="Reset counter for one action")
    args = parser.parse_args()

    rl = RateLimiter()

    if args.reset:
        rl.reset(args.reset)
    elif args.action:
        rem = rl.remaining(args.action)
        max_count = rl._limits.get(args.action, DEFAULT_MAX)
        used = max_count - rem
        print(f"{args.action}: {used}/{max_count} used  ({rem} remaining)")
    else:
        st = rl.status()
        if not st:
            print("No rate limit data recorded yet.")
        else:
            print(f"{'Action':<20} {'Used':>6} {'Max/hr':>8} {'Remaining':>10} {'% Used':>8}")
            print("-" * 58)
            for action, info in sorted(st.items()):
                bar = "#" * (info["pct_used"] // 10) + "-" * (10 - info["pct_used"] // 10)
                print(
                    f"{action:<20} {info['used']:>6} {info['max_per_hour']:>8} "
                    f"{info['remaining']:>10}   [{bar}]"
                )
