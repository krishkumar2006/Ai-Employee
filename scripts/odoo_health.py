"""
odoo_health.py — Platinum Tier
================================
Health monitoring script for the self-hosted Odoo Docker instance.
Runs every 5 minutes via cron. Writes JSON health report and sends
Telegram alerts on failures.

Checks performed:
  1. Odoo HTTP reachability  (GET /web/health)
  2. Odoo JSON-RPC auth      (authenticate call — verifies DB + credentials)
  3. Docker container status (postgres, odoo, nginx)
  4. Disk usage              (alert if > 80%)
  5. SSL certificate expiry  (alert if < 14 days)
  6. Last backup age         (alert if > 25 hours)

Output:
  vault/Logs/HEALTH_ODOO.json   — machine-readable health snapshot
  vault/Logs/odoo_health.log    — human-readable run log

Alerts:
  Telegram bot (free) — configure BOT_TOKEN + CHAT_ID in .env or .env.cloud

Usage:
  python scripts/odoo_health.py              # one-shot check
  python scripts/odoo_health.py --verbose    # print full details to stdout
  python scripts/odoo_health.py --quiet      # only print on failure

Cron (every 5 min):
  */5 * * * * /path/.venv/bin/python /path/scripts/odoo_health.py --quiet
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VAULT        = PROJECT_ROOT / "vault"
LOGS_DIR     = VAULT / "Logs"
HEALTH_FILE  = LOGS_DIR / "HEALTH_ODOO.json"
LOG_FILE     = LOGS_DIR / "odoo_health.log"

PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Config (from env / .env.cloud)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from config import cfg
    ODOO_URL      = cfg.get("ODOO_URL",      "http://localhost:8069")
    ODOO_DB       = cfg.get("ODOO_DB",       "odoo")
    ODOO_USER     = cfg.get("ODOO_USER",     "")
    ODOO_PASSWORD = cfg.get("ODOO_PASSWORD", "")
    TELEGRAM_BOT  = cfg.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT = cfg.get("TELEGRAM_CHAT_ID",   "")
except ImportError:
    ODOO_URL      = os.environ.get("ODOO_URL", "http://localhost:8069")
    ODOO_DB       = os.environ.get("ODOO_DB",  "odoo")
    ODOO_USER     = os.environ.get("ODOO_USER", "")
    ODOO_PASSWORD = os.environ.get("ODOO_PASSWORD", "")
    TELEGRAM_BOT  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID",   "")

COMPOSE_PROJECT   = "ai-employee-odoo"
DISK_WARN_PCT     = 80
BACKUP_MAX_AGE_H  = 25
SSL_WARN_DAYS     = 14

CONTAINERS = [
    f"{COMPOSE_PROJECT}-postgres-1",
    f"{COMPOSE_PROJECT}-odoo-1",
    f"{COMPOSE_PROJECT}-nginx-1",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now(tz=PKT).isoformat()


def log(msg: str, verbose: bool = False, quiet: bool = False) -> None:
    line = f"[odoo_health {ts()}] {msg}"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    if not quiet or "[FAIL]" in msg or "[WARN]" in msg:
        print(line)


def send_telegram(message: str) -> None:
    """Send a Telegram alert (free, no paid service)."""
    if not TELEGRAM_BOT or not TELEGRAM_CHAT:
        return
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage"
        data = json.dumps({"chat_id": TELEGRAM_CHAT, "text": message,
                           "parse_mode": "HTML"}).encode()
        req  = urllib.request.Request(url, data=data,
                                       headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"[WARN] Telegram alert failed: {e}")


# ---------------------------------------------------------------------------
# Check 1: Odoo HTTP
# ---------------------------------------------------------------------------

def check_odoo_http() -> dict[str, Any]:
    url = f"{ODOO_URL}/web/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "odoo-health-check"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            body   = resp.read(256).decode(errors="replace")
        ok = status == 200 and ("status" in body.lower() or "ok" in body.lower())
        return {"status": "ok" if ok else "warn", "http_code": status,
                "url": url, "body_preview": body[:80]}
    except urllib.error.URLError as e:
        return {"status": "fail", "error": str(e), "url": url}
    except Exception as e:
        return {"status": "fail", "error": str(e), "url": url}


# ---------------------------------------------------------------------------
# Check 2: Odoo JSON-RPC auth
# ---------------------------------------------------------------------------

def check_odoo_rpc() -> dict[str, Any]:
    if not ODOO_USER or not ODOO_PASSWORD:
        return {"status": "skip", "reason": "ODOO_USER/ODOO_PASSWORD not set"}

    url     = f"{ODOO_URL}/jsonrpc"
    payload = {
        "jsonrpc": "2.0",
        "method":  "call",
        "id":      1,
        "params": {
            "service": "common",
            "method":  "authenticate",
            "args":    [ODOO_DB, ODOO_USER, ODOO_PASSWORD, {}],
        },
    }
    try:
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read())

        uid = body.get("result")
        if uid and isinstance(uid, int):
            return {"status": "ok", "uid": uid, "db": ODOO_DB}
        err = body.get("error", {})
        return {"status": "fail",
                "error": err.get("data", {}).get("message", str(err))}
    except Exception as e:
        return {"status": "fail", "error": str(e)}


# ---------------------------------------------------------------------------
# Check 3: Docker containers
# ---------------------------------------------------------------------------

def check_docker_containers() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=15
        )
        running: dict[str, str] = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                running[parts[0]] = parts[1]

        statuses: dict[str, str] = {}
        all_up = True
        for name in CONTAINERS:
            if name in running:
                statuses[name] = running[name]
            else:
                statuses[name] = "NOT RUNNING"
                all_up = False

        return {"status": "ok" if all_up else "fail", "containers": statuses}

    except FileNotFoundError:
        return {"status": "skip", "reason": "docker CLI not found"}
    except Exception as e:
        return {"status": "warn", "error": str(e)}


# ---------------------------------------------------------------------------
# Check 4: Disk usage
# ---------------------------------------------------------------------------

def check_disk() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["df", "-h", "/"],
            capture_output=True, text=True, timeout=5
        )
        lines  = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            # Find Use% column by looking for the part ending in '%'
            use_pct_str = next((p for p in parts if p.endswith("%")), None)
            if use_pct_str is None:
                return {"status": "skip", "reason": "Could not parse df output"}
            use_pct = int(use_pct_str.strip("%"))
            return {
                "status":  "ok" if use_pct < DISK_WARN_PCT else "warn",
                "used_pct": use_pct,
                "total":    parts[1] if len(parts) > 1 else "?",
                "used":     parts[2] if len(parts) > 2 else "?",
                "avail":    parts[3] if len(parts) > 3 else "?",
            }
    except Exception as e:
        return {"status": "warn", "error": str(e)}
    return {"status": "skip"}


# ---------------------------------------------------------------------------
# Check 5: SSL certificate expiry
# ---------------------------------------------------------------------------

def check_ssl_cert(domain: str) -> dict[str, Any]:
    if not domain or domain in ("localhost", "127.0.0.1"):
        return {"status": "skip", "reason": "no domain configured"}
    try:
        import ssl
        ctx  = ssl.create_default_context()
        conn = ctx.wrap_socket(
            __import__("socket").create_connection((domain, 443), timeout=10),
            server_hostname=domain
        )
        cert  = conn.getpeercert()
        conn.close()
        expires_str = cert["notAfter"]   # e.g. "Mar 15 12:00:00 2026 GMT"
        expires     = datetime.strptime(expires_str, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        days_left = (expires - datetime.now(tz=timezone.utc)).days
        return {
            "status":    "ok" if days_left > SSL_WARN_DAYS else "warn",
            "expires":   expires.isoformat(),
            "days_left": days_left,
            "domain":    domain,
        }
    except Exception as e:
        return {"status": "warn", "error": str(e), "domain": domain}


# ---------------------------------------------------------------------------
# Check 6: Last backup age
# ---------------------------------------------------------------------------

def check_backup_age() -> dict[str, Any]:
    backup_health = LOGS_DIR / "HEALTH_ODOO_BACKUP.json"
    if not backup_health.exists():
        return {"status": "warn", "reason": "No backup health file found — run odoo_backup.sh"}

    try:
        data       = json.loads(backup_health.read_text(encoding="utf-8"))
        last_utc   = data.get("last_backup_utc", "")
        if not last_utc:
            return {"status": "warn", "reason": "last_backup_utc missing"}

        last_dt    = datetime.fromisoformat(last_utc.replace("Z", "+00:00"))
        age_hours  = (datetime.now(tz=timezone.utc) - last_dt).total_seconds() / 3600
        return {
            "status":     "ok" if age_hours < BACKUP_MAX_AGE_H else "warn",
            "last_backup": last_utc,
            "age_hours":   round(age_hours, 1),
            "db_size":     data.get("db_size", "?"),
        }
    except Exception as e:
        return {"status": "warn", "error": str(e)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_checks(verbose: bool = False, quiet: bool = False) -> dict[str, Any]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    now    = datetime.now(tz=PKT)
    domain = ""

    # Try to get domain from .env.docker
    env_docker = PROJECT_ROOT / "docker" / ".env.docker"
    if env_docker.exists():
        for line in env_docker.read_text(encoding="utf-8").splitlines():
            if line.startswith("DOMAIN="):
                domain = line.split("=", 1)[1].strip()
                break

    log(f"Starting health check | Odoo: {ODOO_URL}", verbose=verbose, quiet=quiet)

    checks = {
        "odoo_http":    check_odoo_http(),
        "odoo_rpc":     check_odoo_rpc(),
        "docker":       check_docker_containers(),
        "disk":         check_disk(),
        "ssl_cert":     check_ssl_cert(domain),
        "backup_age":   check_backup_age(),
    }

    # Determine overall status
    statuses = [c.get("status", "skip") for c in checks.values()]
    if "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "ok"

    health: dict[str, Any] = {
        "timestamp":    now.isoformat(),
        "overall":      overall,
        "odoo_url":     ODOO_URL,
        "domain":       domain or "(local)",
        "checks":       checks,
    }

    # Write health file
    HEALTH_FILE.write_text(json.dumps(health, indent=2), encoding="utf-8")

    # Log each check result
    for name, result in checks.items():
        status = result.get("status", "skip")
        icon   = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}
        extra  = ""
        if "error" in result:
            extra = f" — {result['error']}"
        elif "days_left" in result:
            extra = f" — {result['days_left']} days until expiry"
        elif "uid" in result:
            extra = f" — authenticated as uid={result['uid']}"
        elif "used_pct" in result:
            extra = f" — {result['used_pct']}% used ({result.get('avail', '?')} free)"
        elif "age_hours" in result:
            extra = f" — last backup {result['age_hours']}h ago"
        log(f"{icon.get(status,'[????]')} {name:<15}{extra}", verbose=verbose, quiet=quiet)

    log(f"Overall: {overall.upper()}", verbose=verbose, quiet=quiet)

    # Telegram alert on failure
    if overall in ("fail", "warn") and TELEGRAM_BOT:
        failing = [
            f"• {name}: {r.get('error', r.get('reason', r.get('status', '?')))}"
            for name, r in checks.items()
            if r.get("status") in ("fail", "warn")
        ]
        send_telegram(
            f"[AI-Employee] Odoo Health: <b>{overall.upper()}</b>\n"
            f"Server: {ODOO_URL}\n\n" + "\n".join(failing)
        )

    return health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Odoo health monitor")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print all checks to stdout")
    parser.add_argument("--quiet",   "-q", action="store_true",
                        help="Only print failures and warnings")
    args = parser.parse_args()

    result = run_checks(verbose=args.verbose, quiet=args.quiet)
    sys.exit(0 if result["overall"] == "ok" else 1)
