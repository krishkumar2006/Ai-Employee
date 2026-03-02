# Common Gold Tier Errors & Fixes

Grouped by component. Every entry includes the exact error message, root cause, and the fix.

---

## audit_logger.py

### E-01 — `ModuleNotFoundError: No module named 'audit_logger'`

**Where:** Any watcher or MCP that imports `audit_logger`

**Cause:** Python can't find `audit_logger.py` because the script is run from
the wrong directory, or `sys.path` doesn't include the project root.

**Fix:**
```python
# Add to the TOP of any file that imports audit_logger (before the import)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Now this works:
from audit_logger import AuditLogger, EV_START
```

Or always run scripts from the project root:
```bash
cd "D:\Heck ---0\AI Empolyee"
python watchers/gmail_watcher.py   # NOT: cd watchers && python gmail_watcher.py
```

---

### E-02 — HEALTH.json grows unboundedly

**Cause:** `_update_health()` rewrites the entire file on every log call.
With many components and high log rate, the file can grow to MBs.

**Fix:** This is by design — HEALTH.json only stores the **latest** entry per component
(it's a dict keyed by component name). It does NOT grow unboundedly.
If it's large, check whether `self.component` is accidentally being set to a dynamic
string (e.g., a UUID) instead of a static name.

```python
# Wrong — creates a new key every call:
log = AuditLogger(f"task_{task_id}")

# Right — one key per component type:
log = AuditLogger("odoo_mcp")
log.info(EV_ODOO_ACTION, task_id=task_id)   # task_id in kwargs, not component
```

---

### E-03 — Log file not created / silent failure

**Cause:** `AuditLogger` swallows all exceptions by design (`except Exception: pass`)
to never crash the caller. If `vault/Logs/` can't be created, logs are silently dropped.

**Diagnosis:**
```python
from pathlib import Path
LOGS_DIR = Path("vault/Logs")
print("LOGS_DIR exists:", LOGS_DIR.exists())
print("Writable:", LOGS_DIR.stat() if LOGS_DIR.exists() else "missing")
```

**Fix:**
```bash
mkdir -p vault/Logs   # Linux/Mac
# Windows:
python -c "from pathlib import Path; Path('vault/Logs').mkdir(parents=True, exist_ok=True)"
```

---

## retry_handler.py

### E-04 — `@retry` not retrying — fails immediately

**Cause:** The exception type you're raising is not in the `exceptions` tuple.
Default is `(Exception,)` which should catch everything, but `BaseException`
subclasses (like `SystemExit`, `KeyboardInterrupt`) are not caught.

**Diagnosis:**
```python
# Check what exception type is actually raised
try:
    call_odoo()
except Exception as e:
    print(type(e).__name__, e)
```

**Fix:**
```python
# If your function raises a custom exception not inheriting from Exception:
@retry(service="odoo", exceptions=(OdooError, requests.RequestException))
def call_odoo():
    ...
```

---

### E-05 — `CircuitOpenError` raised immediately on first call

**Cause:** A previous session opened the circuit and the `CircuitBreaker` singleton
persists in memory. Because `CircuitBreaker._registry` is a class-level dict, it
survives for the lifetime of the process.

**Fix:** Manually reset the circuit if you know the service is back:
```python
from retry_handler import CircuitBreaker
cb = CircuitBreaker.get("odoo")
print("State:", cb.state)   # Confirm it's OPEN
cb.reset()                  # Force close
print("State after reset:", cb.state)
```

Or wait for the `recovery_timeout` (120s for Odoo) — the breaker auto-transitions
to HALF_OPEN and allows one probe call through.

---

### E-06 — Retry sleeping too long during testing

**Cause:** The service-level `base_delay` and `max_delay` are tuned for production.
During development, 30s base delays on social APIs are painful.

**Fix:** Override for testing only:
```python
# Temporary override — do NOT commit this
from retry_handler import RETRY_CONFIGS
RETRY_CONFIGS["meta"]["base_delay"] = 0.5
RETRY_CONFIGS["meta"]["max_delay"]  = 2.0
```

Or use `max_attempts=1` for unit tests:
```python
@retry(service="default", max_attempts=1)  # no retry in tests
def call_api_in_test():
    ...
```

---

### E-07 — `jitter` causes unpredictable wait times in logs

**Cause:** Jitter multiplies delay by `[0.5×, 1.5×]` — intentional, but can make
logs hard to compare across runs.

**Fix:** For log-stable testing, set `jitter=False`:
```python
@retry(service="gmail", jitter=False)
def fetch_emails():
    ...
```

---

## offline_queue.py

### E-08 — Queue items never drained (queue grows forever)

**Cause:** Nobody is calling `q.drain(executor_fn)`. The queue is enqueue-only
unless something drains it.

**Fix:** Add a drain call to the orchestrator's recovery flow.
In `orchestrator.py`, add to `health_check_watchers()` or create a new scheduled task:

```python
# In orchestrator.py — add to setup_schedule():
schedule.every(15).minutes.do(drain_offline_queues)

def drain_offline_queues():
    from offline_queue import get_queue
    from retry_handler import CircuitBreaker

    cb = CircuitBreaker.get("odoo")
    if cb.state != "CLOSED":
        logger.info("Odoo circuit not CLOSED — skipping queue drain")
        return

    q = get_queue("odoo")
    if q.pending_count() == 0:
        return

    logger.info("Draining Odoo queue: %d items", q.pending_count())
    # Wire executor_fn to your actual Odoo create functions here
    counts = q.drain(my_odoo_executor)
    logger.info("Drain result: %s", counts)
```

---

### E-09 — `KeyError` or `json.JSONDecodeError` on drain

**Cause:** A queue file was written partially (process killed mid-write) or is corrupted.

**Fix:** `drain()` already handles this — corrupted files are deleted silently.
But to inspect:
```python
from pathlib import Path
import json
for f in Path("vault/Queue").glob("odoo_*.json"):
    try:
        json.loads(f.read_text())
        print("OK:", f.name)
    except json.JSONDecodeError as e:
        print("CORRUPT:", f.name, e)
        f.unlink()   # Delete and move on
```

---

### E-10 — Queue items expired before drain

**Cause:** `EXPIRY_HOURS = 72` — items older than 72 hours are discarded on drain.

**Fix:** If you need longer retention, change `EXPIRY_HOURS` in `offline_queue.py`:
```python
EXPIRY_HOURS: int = 168   # 7 days
```

Or monitor queue depth in the CEO Briefing / health check so you catch stale queues
before they expire.

---

## watchdog.py

### E-11 — `orchestrator.py` not found — watchdog exits silently

**Cause:** `watchdog.py` is run from the wrong directory, so
`Path(__file__).resolve().parent / "orchestrator.py"` resolves incorrectly.

**Fix:** Always run from the project root:
```bash
cd "D:\Heck ---0\AI Empolyee"
python watchdog.py
```

Or use an absolute path override in `SUPERVISED`:
```python
SUPERVISED = [
    {
        "name": "orchestrator",
        "script": r"D:\Heck ---0\AI Empolyee\orchestrator.py",
        "enabled": True,
        ...
    }
]
```

---

### E-12 — Watchdog: max restarts exceeded — orchestrator not coming back

**Cause:** A persistent bug in `orchestrator.py` causes it to crash immediately
on startup. After 20 restarts the watchdog gives up.

**Diagnosis:**
```bash
# Check the orchestrator log for the crash traceback
type logs\orchestrator.log | findstr "Traceback" /A:5
```

**Fix the root cause**, then restart the watchdog:
```bash
# After fixing orchestrator.py:
python watchdog.py   # watchdog resets _restart_counts on fresh start
```

To temporarily raise the limit without fixing code:
```python
# In SUPERVISED config:
"max_restarts": 50
```

---

### E-13 — Watchdog and orchestrator both running separately — double watchers

**Cause:** You started `orchestrator.py` manually AND then started `watchdog.py`,
which launches another orchestrator. Now two orchestrators run and each manages
its own set of watcher children.

**Fix:** Stop all processes and use only one entry point:
```bash
# Windows: kill by name
taskkill /IM python.exe /F

# Then start cleanly
python watchdog.py   # watchdog manages orchestrator manages watchers
```

---

## odoo_mcp.py

### E-14 — `Odoo RPC error: Access Denied`

**Cause:** Wrong credentials in `.claude/mcp.json` or the Odoo user doesn't have
accounting permissions.

**Fix:**
```python
# Test credentials directly
import os, requests
r = requests.post("http://localhost:8069/jsonrpc", json={
    "jsonrpc": "2.0", "method": "call",
    "params": {"service": "common", "method": "authenticate",
               "args": ["ai-employee", "admin@example.com", "wrongpass", {}]},
    "id": 1
})
print(r.json())   # {"result": False} if creds wrong, int uid if correct
```

Then update `.claude/mcp.json` with the correct `ODOO_PASSWORD`.

---

### E-15 — `Odoo RPC error: No partner found matching '...'`

**Cause:** The customer name given to Claude doesn't exactly match an Odoo contact.
`odoo_draft_invoice` uses `ilike` (case-insensitive partial match) but if no records
exist at all, it fails.

**Fix:** Create the partner in Odoo first:
```
Odoo UI → Contacts → New → Enter "Acme Corp" → Save
```

Then retry. The `ilike` search will find it.

---

### E-16 — MCP tool call hangs indefinitely

**Cause:** The MCP server process (`odoo_mcp.py`) is waiting on a blocked
`requests.post()` with no timeout, or Odoo is slow.

**Fix:** `jsonrpc()` in `odoo_mcp.py` uses `timeout=30`. If Odoo is still hanging,
check Odoo server load. The `@retry(service="odoo")` decorator adds up to 5 retries.

To diagnose: check `vault/Logs/AUDIT_*.jsonl` for `api_call` events without
matching `api_call_failed` — indicates the call is in flight.

---

### E-17 — `draft_id not found` error when confirming

**Cause:** The MCP server was restarted between the draft call and confirm call.
The in-memory `drafts: dict` is wiped on restart.

**Fix:** The draft vault file (`vault/Odoo_Drafts/ODOO_invoice_*.md`) still exists.
Recreate the draft manually or check the vault file for the draft details:
```bash
dir vault\Odoo_Drafts\
```

For production, enhance `odoo_mcp.py` to reload drafts from vault on startup
(not implemented by default — HITL safety feature keeps drafts ephemeral).

---

### E-18 — `account.journal` not found — payment fails

**Cause:** No bank journal is configured in Odoo, so `odoo_confirm_payment`
can't find a journal ID.

**Fix:** In Odoo UI → Accounting → Configuration → Journals → Create "Bank" journal.
The MCP searches for `type = bank` — at least one must exist.

---

## gmail_watcher.py

### E-19 — `credentials.json not found` — watcher exits

**Cause:** OAuth credentials file missing from `watchers/` directory.

**Fix:**
1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID (Desktop app)
3. Download JSON → rename to `credentials.json` → place in `watchers/`
4. Delete `watchers/gmail_token.json` if it exists (stale token)
5. Restart the watcher — browser opens for consent

---

### E-20 — Gmail token expired silently — no emails detected

**Cause:** `gmail_token.json` contains an expired refresh token.
The watcher catches the refresh error and returns an empty list.

**Diagnosis:**
```python
from pathlib import Path
import json
token = Path("watchers/gmail_token.json")
if token.exists():
    data = json.loads(token.read_text())
    print("Token expiry:", data.get("expiry"))
    print("Has refresh_token:", bool(data.get("refresh_token")))
```

**Fix:** Delete the token file and re-authenticate:
```bash
python -c "from pathlib import Path; Path('watchers/gmail_token.json').unlink(missing_ok=True); print('Token deleted')"
python watchers/gmail_watcher.py   # Browser opens for re-auth
```

---

### E-21 — Emails processed in every poll — infinite duplicates

**Cause:** `watchers/.gmail_processed_ids` file is deleted or not persisting.

**Diagnosis:**
```python
from pathlib import Path
ids_file = Path("watchers/.gmail_processed_ids")
if ids_file.exists():
    ids = ids_file.read_text().splitlines()
    print(f"{len(ids)} IDs tracked")
else:
    print("State file missing — will reprocess all emails on next start")
```

**Fix:** Ensure `watchers/` is writable and `.gmail_processed_ids` is not in `.gitignore`.
If you deliberately cleared it and want to avoid reprocessing old emails, add them manually:
```python
# Mark all current unread as processed without creating cards
# (run before starting the watcher for the first time)
```

---

## filesystem_watcher.py

### E-22 — Watcher misses files dropped too quickly

**Cause:** OS file-write events fire before the file is fully flushed to disk.
`file_path.stat().st_size` returns 0 for a brief moment.

**Fix:** Add a small sleep before reading size in `build_task_content()`:
```python
import time
time.sleep(0.1)   # Wait for file system to flush
size_bytes = file_path.stat().st_size
```

---

### E-23 — `FileNotFoundError` in `build_task_content`

**Cause:** The detected file was deleted (or moved) between the `on_created` event
and the `stat()` call. Happens with antivirus quarantine or temp files.

**Fix:** Already handled by the `except Exception` in `on_created`. To add a check:
```python
if not file_path.exists():
    logger.info("File gone before processing: %s", file_path.name)
    return
```

---

## orchestrator.py

### E-24 — `schedule` library not installed

```
ModuleNotFoundError: No module named 'schedule'
```

**Fix:**
```bash
pip install schedule
```

---

### E-25 — Morning briefing runs every 30 minutes instead of once

**Cause:** The schedule library fires `every().day.at("08:00")` correctly, but if
`briefing_path.exists()` check fails (e.g., Plans/ doesn't exist), it may re-run.

**Fix:** Ensure `PLANS_PATH` exists before the check:
```python
PLANS_PATH.mkdir(parents=True, exist_ok=True)
if briefing_path.exists():
    ...
```

---

### E-26 — Watcher subprocess stdout/stderr swallowed — hard to debug

**Cause:** The orchestrator opens log files for stdout but not stderr.
Error tracebacks go nowhere.

**Fix:** Change `start_watcher()` to redirect stderr to the same log:
```python
proc = subprocess.Popen(
    [sys.executable, script],
    stdout=open(log_file, "a", encoding="utf-8"),
    stderr=subprocess.STDOUT,   # Already correct — both go to log
    cwd=str(PROJECT_ROOT),
)
```

If still not seeing errors:
```bash
type logs\gmail_watcher.log    # Windows
cat logs/gmail_watcher.log     # Linux/Mac
```

---

## ceo_briefing.py

### E-27 — CEO Briefing skipped — "already exists today"

**Cause:** A partial/empty briefing was created earlier. The `output_path.exists()`
check prevents re-running.

**Fix:** Use `--force`:
```bash
python watchers/ceo_briefing.py --force
```

Or delete the partial file:
```bash
python -c "
import datetime
from pathlib import Path
today = datetime.date.today().isoformat()
f = Path(f'vault/Plans/CEO_BRIEFING_{today}.md')
f.unlink(missing_ok=True)
print('Deleted:', f.name)
"
```

---

### E-28 — CEO Briefing: `dotenv` not installed

```
ModuleNotFoundError: No module named 'dotenv'
```

**Fix:**
```bash
pip install python-dotenv
```

---

### E-29 — CEO Briefing generates but has no KPI data

**Cause:** `--no-odoo` flag passed, or Odoo circuit is OPEN.

**Fix:**
```bash
# Run with Odoo enabled and circuit reset
python -c "from retry_handler import CircuitBreaker; CircuitBreaker.get('odoo').reset()"
python watchers/ceo_briefing.py --force
```

---

## General / MCP

### E-30 — MCP server crashes on startup — `mcp` module not found

```
ModuleNotFoundError: No module named 'mcp'
```

**Fix:**
```bash
pip install mcp
# or specifically FastMCP
pip install "mcp[fastmcp]"
```

---

### E-31 — `claude --print` hangs forever in subprocess calls

**Cause:** Claude CLI waiting for interactive input, or `--print` flag not supported
in the installed version.

**Diagnosis:**
```bash
claude --version
claude --help | findstr "print"
```

**Fix:** Ensure Claude Code is up to date:
```bash
npm install -g @anthropic-ai/claude-code
```

If `--print` is not available, use `--output-format text` or check the CLI docs.

---

### E-32 — Social post published twice (deduplication failure)

**Cause:** Content hash not computed, or `vault/Meta_Posted/` hash log missing.

**Fix:** Check `meta_poster.py` deduplication logic. Ensure `vault/Meta_Posted/`
exists and is writable. Check for duplicate `status: ready` drafts in `vault/Meta_Drafts/`.

---

## Diagnostic Commands

```bash
# Full module import check
python -c "
import sys
sys.path.insert(0, '.')
mods = ['audit_logger','retry_handler','offline_queue']
for m in mods:
    try:
        __import__(m)
        print(f'OK  {m}')
    except Exception as e:
        print(f'FAIL {m}: {e}')
"

# Show circuit breaker states (in-process only — resets on restart)
python -c "
from retry_handler import CircuitBreaker
for name in ['odoo','gmail','meta','twitter']:
    cb = CircuitBreaker.get(name)
    print(f'{name:12} {cb.state}')
"

# Show offline queue depths
python -c "
from offline_queue import get_queue
for svc in ['odoo','gmail','meta','twitter']:
    q = get_queue(svc)
    n = q.pending_count()
    if n: print(f'{svc}: {n} items pending')
"

# Tail today's audit log
python -c "
import json, datetime
from pathlib import Path
today = datetime.date.today().isoformat()
f = Path(f'vault/Logs/AUDIT_{today}.jsonl')
if f.exists():
    lines = f.read_text().splitlines()[-20:]
    for l in lines:
        e = json.loads(l)
        print(f'[{e[\"severity\"]:8}] {e[\"component\"]:25} {e[\"event\"]:25} {e.get(\"error\",\"\")}')
else:
    print('No audit log yet today')
"
```
