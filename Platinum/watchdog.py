"""
Watchdog — Gold Tier
=====================
Standalone process supervisor. Monitors the orchestrator (and any other
registered processes) and restarts them on crash with exponential backoff.

The orchestrator already supervises its own child watchers every 5 minutes.
The watchdog's job is to supervise the orchestrator itself — so the entire
system can self-heal from a top-level crash without human intervention.

Usage:
    python watchdog.py            # Run in foreground (normal mode)
    python watchdog.py --once     # Single health-check pass, then exit
    python watchdog.py --no-start # Health-check only; don't auto-launch

Architecture:
    - Supervised processes are listed in SUPERVISED below.
    - Each process gets its own log file in logs/.
    - Restarts use exponential backoff (configurable per process).
    - HEALTH.json staleness is checked every minute.
    - All events written to vault/Logs/AUDIT_YYYY-MM-DD.jsonl via AuditLogger.

Part of the Personal AI Employee system.
"""

import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from audit_logger import (
    AuditLogger,
    EV_ALERT,
    EV_CRASH,
    EV_HEALTH_CHECK,
    EV_RESTART,
    EV_START,
    EV_STOP,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
VAULT_PATH   = PROJECT_ROOT / "vault"
LOGS_DIR     = VAULT_PATH / "Logs"
HEALTH_FILE  = LOGS_DIR / "HEALTH.json"
LOG_DIR      = PROJECT_ROOT / "logs"

LOG_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Audit logger for the watchdog itself
# ---------------------------------------------------------------------------
log = AuditLogger("watchdog")

# ---------------------------------------------------------------------------
# Supervised process registry
# ---------------------------------------------------------------------------
# Add entries here for any long-running process you want the watchdog to manage.
# The orchestrator manages its own child watchers; we manage the orchestrator.
SUPERVISED: list[dict] = [
    {
        "name":               "orchestrator",
        "script":             str(PROJECT_ROOT / "orchestrator.py"),
        "enabled":            True,
        "max_restarts":       20,       # Give up after this many restarts per session
        "restart_delay_base": 5.0,      # Seconds before first restart attempt
        "restart_delay_max":  300.0,    # Cap at 5 minutes between retries
    },
    # Uncomment to also supervise the ralph_loop if run standalone:
    # {
    #     "name":               "ralph_loop",
    #     "script":             str(PROJECT_ROOT / "ralph_loop.py"),
    #     "enabled":            False,
    #     "max_restarts":       5,
    #     "restart_delay_base": 10.0,
    #     "restart_delay_max":  120.0,
    # },
]

# ---------------------------------------------------------------------------
# Watchdog settings
# ---------------------------------------------------------------------------
TICK_INTERVAL         = 10    # Seconds between supervisor ticks
HEALTH_CHECK_TICKS    = 6     # Check HEALTH.json staleness every N ticks (~1 min)
STALENESS_WARN_SECS   = 600   # Warn if a component hasn't logged for this long

# ---------------------------------------------------------------------------
# Runtime state (module-level, single-process)
# ---------------------------------------------------------------------------
_processes:      dict[str, subprocess.Popen] = {}
_restart_counts: dict[str, int]              = {}
_running:        bool                        = True


# ---------------------------------------------------------------------------
# Backoff helper
# ---------------------------------------------------------------------------

def _backoff_delay(name: str, config: dict) -> float:
    """Exponential backoff: base * 2^N, capped at max."""
    count = _restart_counts.get(name, 0)
    base  = config.get("restart_delay_base", 5.0)
    cap   = config.get("restart_delay_max",  300.0)
    return min(base * (2 ** count), cap)


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

def start_process(config: dict) -> Optional[subprocess.Popen]:
    """Launch a supervised process; return the Popen handle, or None on error."""
    name   = config["name"]
    script = config["script"]

    if not Path(script).exists():
        log.error(EV_CRASH, process=name, reason="script_not_found", script=script)
        return None

    log_file = LOG_DIR / f"{name}.log"
    log.info(EV_START, process=name, script=Path(script).name)

    try:
        proc = subprocess.Popen(
            [sys.executable, script],
            stdout=open(log_file, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        log.info(EV_START, process=name, pid=proc.pid, log=log_file.name)
        return proc
    except Exception as exc:
        log.exception(EV_CRASH, exc, process=name)
        return None


def check_and_restart(config: dict) -> None:
    """If a supervised process has crashed, restart it with backoff."""
    name    = config["name"]
    max_r   = config.get("max_restarts", 10)
    proc    = _processes.get(name)
    running = proc is not None and proc.poll() is None

    if running:
        return  # All good

    # Process is down
    exit_code = proc.returncode if proc else "never_started"
    restarts  = _restart_counts.get(name, 0)

    if restarts >= max_r:
        log.critical(
            EV_ALERT,
            process=name,
            reason="max_restarts_exceeded",
            restart_count=restarts,
            exit_code=exit_code,
            message=(
                f"Process '{name}' has crashed {restarts} times. "
                "Watchdog will no longer restart it. Manual intervention required."
            ),
        )
        return

    delay = _backoff_delay(name, config)
    log.warn(
        EV_CRASH,
        process=name,
        exit_code=exit_code,
        restart_attempt=restarts + 1,
        backoff_seconds=round(delay, 1),
    )

    if delay > 0:
        time.sleep(delay)

    new_proc = start_process(config)
    if new_proc:
        _processes[name]      = new_proc
        _restart_counts[name] = restarts + 1
        log.info(
            EV_RESTART,
            process=name,
            pid=new_proc.pid,
            restart_count=_restart_counts[name],
        )
    else:
        _restart_counts[name] = restarts + 1


# ---------------------------------------------------------------------------
# Health staleness check
# ---------------------------------------------------------------------------

def check_health_staleness() -> None:
    """Alert if any component in HEALTH.json hasn't logged recently."""
    if not HEALTH_FILE.exists():
        return

    try:
        health = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.exception(EV_HEALTH_CHECK, exc, file="HEALTH.json",
                      reason="parse_error")
        return

    now = datetime.now(tz=PKT)
    component_status = {}

    for component, info in health.items():
        last_seen_str = info.get("last_seen", "")
        if not last_seen_str:
            continue
        try:
            last_seen = datetime.fromisoformat(last_seen_str)
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=PKT)
            staleness = int((now - last_seen).total_seconds())
            component_status[component] = staleness

            if staleness > STALENESS_WARN_SECS:
                log.warn(
                    EV_ALERT,
                    component=component,
                    last_seen=last_seen_str,
                    staleness_seconds=staleness,
                    threshold_seconds=STALENESS_WARN_SECS,
                    message="Component has not logged recently — possible hang or crash.",
                )
        except Exception:
            pass

    log.info(EV_HEALTH_CHECK, component_staleness_seconds=component_status)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

def handle_shutdown(signum, frame) -> None:
    """Terminate all supervised processes and exit cleanly."""
    global _running
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    log.info(EV_STOP, reason=f"signal_{sig_name}")
    _running = False

    for name, proc in _processes.items():
        if proc and proc.poll() is None:
            log.info(EV_STOP, process=name, pid=proc.pid, action="terminate")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                log.warn(EV_STOP, process=name, action="force_kill")
                proc.kill()

    sys.exit(0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    once_mode     = "--once"     in sys.argv
    no_start_mode = "--no-start" in sys.argv

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT,  handle_shutdown)

    enabled = [c for c in SUPERVISED if c["enabled"]]

    print()
    print("=" * 58)
    print("  AI Employee — Watchdog (Gold Tier)")
    print("=" * 58)
    print(f"  Supervising : {len(enabled)} process(es): "
          f"{', '.join(c['name'] for c in enabled)}")
    print(f"  Tick        : every {TICK_INTERVAL}s")
    print(f"  Health warn : after {STALENESS_WARN_SECS}s silence")
    print(f"  Mode        : {'once' if once_mode else 'no-start' if no_start_mode else 'continuous'}")
    print("  Press Ctrl+C to stop")
    print("=" * 58)
    print()

    log.info(
        EV_START,
        mode="once" if once_mode else "no_start" if no_start_mode else "continuous",
        supervised=[c["name"] for c in enabled],
    )

    # ------------------------------------------------------------------
    # --once: single health pass, no process launching
    # ------------------------------------------------------------------
    if once_mode:
        check_health_staleness()
        for config in SUPERVISED:
            if config["enabled"]:
                proc = _processes.get(config["name"])
                running = proc is not None and proc.poll() is None
                log.info(
                    EV_HEALTH_CHECK,
                    process=config["name"],
                    running=running,
                )
        log.info(EV_STOP, mode="once", reason="completed")
        return

    # ------------------------------------------------------------------
    # Launch supervised processes (unless --no-start)
    # ------------------------------------------------------------------
    if not no_start_mode:
        for config in enabled:
            proc = start_process(config)
            if proc:
                _processes[config["name"]] = proc
                _restart_counts[config["name"]] = 0

    # ------------------------------------------------------------------
    # Main supervision loop
    # ------------------------------------------------------------------
    tick = 0
    while _running:
        time.sleep(TICK_INTERVAL)
        tick += 1

        for config in SUPERVISED:
            if config["enabled"]:
                check_and_restart(config)

        # Health staleness check every HEALTH_CHECK_TICKS ticks
        if tick % HEALTH_CHECK_TICKS == 0:
            check_health_staleness()

            # Log current supervised process states
            states = {
                c["name"]: (
                    _processes.get(c["name"]) is not None
                    and _processes[c["name"]].poll() is None
                )
                for c in SUPERVISED if c["enabled"]
            }
            log.info(EV_HEALTH_CHECK, tick=tick, process_states=states)


if __name__ == "__main__":
    main()
