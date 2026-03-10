# End-to-End Test Guide — Gold Tier

This guide walks through seven concrete test scenarios that cover every major
Gold Tier flow. Run them in order — each one builds confidence in the next.

---

## Prerequisites

```bash
# Verify all modules import cleanly
python -c "
from audit_logger import AuditLogger, EV_START
from retry_handler import CircuitBreaker, retry, CircuitOpenError
from offline_queue import get_queue
print('All Gold Tier imports: OK')
"

# Confirm Claude CLI is reachable
claude --version

# Confirm Odoo is reachable (if testing ERP flows)
python -c "
import requests
r = requests.get('http://localhost:8069/web/database/selector', timeout=5)
print('Odoo HTTP:', r.status_code)
"
```

---

## Test 1: File Drop → Task Card → Plan (Bronze/Silver)

**What it validates:** `filesystem_watcher.py` + AuditLogger + Claude plan generation

### Steps

```bash
# Terminal 1: start the filesystem watcher
python watchers/filesystem_watcher.py

# Terminal 2: drop a test file
echo "Q1 2026 Revenue Report" > "vault/Inbox/Q1_Revenue_Report.pdf"
```

### Expected within 3 seconds

```
vault/Needs_Action/FILE_Q1_Revenue_Report.pdf_<ts>.md   ← task card
vault/Plans/FILE_Q1_Revenue_Report.pdf_<ts>_PLAN.md     ← plan (if Claude available)
vault/Logs/AUDIT_<today>.jsonl                           ← EV_TASK_CREATED entry
```

### Verify

```bash
python -c "
from pathlib import Path
cards = list(Path('vault/Needs_Action').glob('FILE_Q1*'))
print('Task card:', cards[0].name if cards else 'NOT FOUND')

import json
log = Path('vault/Logs')
today = __import__('datetime').date.today().isoformat()
audit = log / f'AUDIT_{today}.jsonl'
if audit.exists():
    lines = [json.loads(l) for l in audit.read_text().splitlines() if l]
    ev = [l for l in lines if l.get('event') == 'task_created']
    print('Audit events task_created:', len(ev))
"
```

### Pass Criteria
- `vault/Needs_Action/FILE_Q1*.md` exists with `status: pending` in frontmatter
- `vault/Logs/AUDIT_*.jsonl` contains a `task_created` entry with `component: filesystem_watcher`
- `vault/Plans/FILE_Q1*_PLAN.md` exists if Claude CLI is available (warn if not, not a failure)

---

## Test 2: Email → Task Card → Plan (Silver)

**What it validates:** `gmail_watcher.py` + `@retry(service="gmail")` + CircuitBreaker

> Requires Gmail OAuth credentials in `watchers/credentials.json`.

### Steps

1. Send yourself an email with subject: `URGENT: Test invoice approval needed`
2. Wait 60–90 seconds for the poll cycle

```bash
# Monitor the watcher output in real-time
python watchers/gmail_watcher.py
# (watch the console output)
```

### Expected

```
vault/Needs_Action/EMAIL_URGENT--Test-invoice-approval-needed_<ts>.md
vault/Plans/EMAIL_URGENT_*_PLAN.md
vault/Logs/AUDIT_<today>.jsonl   ← task_created + plan_generated events
```

### Verify

```bash
python -c "
from pathlib import Path
cards = list(Path('vault/Needs_Action').glob('EMAIL_URGENT*'))
if cards:
    text = cards[0].read_text()
    print('Priority:', 'high' if 'priority: high' in text else 'normal')
    print('Source:', 'gmail' if 'source: gmail' in text else '??')
else:
    print('No email task card found yet — wait 60s and retry')
"
```

### Pass Criteria
- Task card created with `priority: high` (URGENT keyword detected)
- `source: gmail` in frontmatter
- Audit log has `task_created` with `component: gmail_watcher`

---

## Test 3: Odoo Invoice via Claude MCP (Gold — HITL)

**What it validates:** `odoo_mcp.py` + CircuitBreaker + retry + AuditLogger + HITL draft flow

> Requires Odoo running at `http://localhost:8069` and MCP configured in `.claude/mcp.json`.

### Step 3a: Draft an Invoice

Open Claude Code in the project directory and type:

```
Create an invoice for Acme Corp for 3 hours of AI consulting at PKR 15,000/hour.
Invoice date today.
```

**Expected Claude response:**
- Calls `odoo_draft_invoice` tool
- Returns a formatted `INVOICE PREVIEW` with draft ID (e.g., `abc12345`)
- Saves `vault/Odoo_Drafts/ODOO_invoice_abc12345_<ts>.md`
- Asks: "Should I create this in Odoo?"

### Step 3b: Approve and Confirm

Reply to Claude:

```
Yes, create it. Draft ID: abc12345
```

**Expected:**
- Calls `odoo_confirm_invoice(draft_id="abc12345")`
- Returns "INVOICE CREATED SUCCESSFULLY IN ODOO"
- Odoo invoice in DRAFT state at `http://localhost:8069`
- `vault/Odoo_Logs/ODOO_LOG_*.json` audit entry created
- `vault/Logs/AUDIT_*.jsonl` has `EV_ODOO_ACTION` with `action: invoice_created`

### Step 3c: Verify in Odoo

```bash
# Check via Python
python -c "
import os, requests
ODOO_URL = os.environ.get('ODOO_URL', 'http://localhost:8069')
ODOO_DB  = os.environ.get('ODOO_DB',  'ai-employee')
ODOO_USER = os.environ.get('ODOO_USER', 'admin@example.com')
ODOO_PASS = os.environ.get('ODOO_PASSWORD', 'admin')

uid = requests.post(f'{ODOO_URL}/jsonrpc', json={'jsonrpc':'2.0','method':'call',
    'params':{'service':'common','method':'authenticate',
    'args':[ODOO_DB, ODOO_USER, ODOO_PASS, {}]},'id':1}).json()['result']

result = requests.post(f'{ODOO_URL}/jsonrpc', json={'jsonrpc':'2.0','method':'call',
    'params':{'service':'object','method':'execute_kw',
    'args':[ODOO_DB, uid, ODOO_PASS, 'account.move', 'search_read',
    [[['move_type','=','out_invoice'],['partner_id.name','ilike','Acme']]],
    {'fields':['name','state','amount_total'],'limit':5}]},'id':2}).json()['result']
print('Invoices found:', result)
"
```

### Pass Criteria
- Invoice exists in Odoo with `state: draft` and `amount_total: 45000`
- `vault/Odoo_Logs/` has a JSON file with `action: invoice_created`
- `vault/Logs/AUDIT_*.jsonl` has `event: odoo_action` with `component: odoo_mcp`

---

## Test 4: Circuit Breaker + Offline Queue (Gold — Resilience)

**What it validates:** `retry_handler.CircuitBreaker` + `offline_queue.OfflineQueue` + graceful degradation

### Step 4a: Break Odoo

Temporarily change the ODOO_URL in `.claude/mcp.json` to a bad port:
```json
"ODOO_URL": "http://localhost:9999"
```

Restart the MCP server (restart Claude Code).

### Step 4b: Trigger a Write Operation

In Claude Code:
```
Create an invoice for Beta Corp for 1 website redesign at PKR 80,000.
Then confirm it.
```

**Expected Claude response:**
- `odoo_draft_invoice` may partially fail (partner lookup) or return with warning
- `odoo_confirm_invoice` hits CircuitOpenError after 5 retries
- Returns: "⚠ Odoo is currently unreachable (circuit OPEN). Operation queued."
- `vault/Queue/odoo_<id>.json` is created on disk

### Step 4c: Verify Queue

```bash
python -c "
from offline_queue import get_queue
q = get_queue('odoo')
print('Queue depth:', q.pending_count())
for item in q.list_items():
    print(f'  [{item[\"id\"]}] {item[\"operation\"]} queued at {item[\"queued_at\"]}')
"
```

### Step 4d: Restore and Drain

Restore the correct `ODOO_URL` and restart Claude Code. Then:

```bash
# Manually drain the queue (or your scheduled task will do this)
python -c "
from offline_queue import get_queue

def replay(operation, payload):
    print(f'Replaying: {operation} → {payload}')
    # In production: call actual Odoo create functions here
    # For the test, just print
    raise NotImplementedError('Wire to real Odoo executor for production drain')

q = get_queue('odoo')
counts = q.drain(replay)
print('Result:', counts)
"
```

### Pass Criteria
- `vault/Queue/odoo_*.json` exists with `operation: create_invoice`
- Audit log has `event: circuit_open` + `event: queue_enqueued`
- Queue count drops to 0 after drain

---

## Test 5: Social Post — Meta/Twitter (Gold)

**What it validates:** Social media posting pipeline
> Requires API credentials in `.claude/mcp.json` or environment.

### Quick Smoke Test (without real API call)

```bash
# Verify meta_poster can connect
python watchers/meta_poster.py verify

# Verify twitter summary
python watchers/twitter_summary.py
```

### Draft → Post Flow

```bash
# Create a test draft in vault/Meta_Drafts/
python -c "
from pathlib import Path
from datetime import datetime, timezone, timedelta
PKT = timezone(timedelta(hours=5))
now = datetime.now(tz=PKT)
draft = '''---
title: Test post from AI Employee
platform: facebook
status: ready
generated_at: {ts}
---

Testing the Personal AI Employee Gold Tier.
Automated content pipeline: Claude generates, human approves, system posts.

#AI #Automation #PersonalAI
'''.format(ts=now.isoformat())
Path('vault/Meta_Drafts').mkdir(parents=True, exist_ok=True)
p = Path('vault/Meta_Drafts') / f'DRAFT_test_{now.strftime(\"%H-%M-%S\")}.md'
p.write_text(draft)
print('Draft created:', p.name)
"

# Run the poster (it picks up status: ready drafts)
python watchers/meta_poster.py
```

### Pass Criteria
- Draft moves from `vault/Meta_Drafts/` to `vault/Meta_Posted/` (or status updated)
- Facebook post visible in Page feed
- `vault/Logs/AUDIT_*.jsonl` has `event: post_published`

---

## Test 6: Weekly CEO Briefing (Gold — End-to-End)

**What it validates:** `ceo_briefing.py` + Odoo data + Claude narrative + Business_Goals.md parsing

### Quick Test (skip Odoo)

```bash
python watchers/ceo_briefing.py --no-odoo --force
```

### Expected

```
vault/Plans/CEO_BRIEFING_<today>.md    ← Full executive briefing
vault/Plans/CEO_BRIEFING_<today>_data.json  ← Raw data companion
```

### Full Test (with Odoo)

```bash
python watchers/ceo_briefing.py --force
```

### Verify

```bash
python -c "
from pathlib import Path
import datetime
today = datetime.date.today().isoformat()
briefing = Path(f'vault/Plans/CEO_BRIEFING_{today}.md')
if briefing.exists():
    text = briefing.read_text()
    print('Briefing size:', len(text), 'chars')
    sections = [l for l in text.splitlines() if l.startswith('##')]
    print('Sections:', sections)
else:
    print('Briefing not found — check logs/ceo_briefing.log')
"
```

### Pass Criteria
- `CEO_BRIEFING_*.md` exists with at least 6 `##` sections
- `## Executive Summary` present
- `## KPI Scorecard` present with PASS/WARN/FAIL ratings
- `## Financial Performance` present with Odoo data (or note if offline)

---

## Test 7: Watchdog Restart (Gold — Self-Healing)

**What it validates:** `watchdog.py` exponential backoff + AuditLogger + process supervision

### Step 7a: Start Watchdog

```bash
# Terminal 1
python watchdog.py
# You should see orchestrator started at PID XXXXX
```

### Step 7b: Kill the Orchestrator

```bash
# Terminal 2 — find the orchestrator PID and kill it
python -c "
from pathlib import Path
import json
health = Path('vault/Logs/HEALTH.json')
if health.exists():
    data = json.loads(health.read_text())
    print('Health snapshot:', json.dumps(data, indent=2))
else:
    print('No HEALTH.json yet')
"
# Then kill manually: taskkill /PID <orchestrator_pid> /F  (Windows)
```

### Step 7c: Observe Restart

**Expected within 5–10 seconds:**
```
[watchdog] WARN  watcher_crash    process=orchestrator exit_code=1 backoff_seconds=5.0
[watchdog] INFO  watcher_start    process=orchestrator script=orchestrator.py
[watchdog] INFO  watcher_restart  process=orchestrator pid=<new_pid> restart_count=1
```

### Step 7d: Verify via Audit Log

```bash
python -c "
import json
from pathlib import Path
import datetime
today = datetime.date.today().isoformat()
audit = Path(f'vault/Logs/AUDIT_{today}.jsonl')
lines = [json.loads(l) for l in audit.read_text().splitlines() if l]
restarts = [l for l in lines if l.get('event') == 'watcher_restart']
print(f'Restarts logged: {len(restarts)}')
for r in restarts:
    print(f'  {r[\"ts\"]} — {r[\"process\"]} PID {r.get(\"pid\")} (restart #{r.get(\"restart_count\")})')
"
```

### Pass Criteria
- Orchestrator restarted within `restart_delay_base` (5s) + jitter
- `vault/Logs/AUDIT_*.jsonl` has `event: watcher_crash` then `event: watcher_restart`
- `vault/Logs/HEALTH.json` shows updated `last_seen` timestamp for `orchestrator`
- Second kill triggers 10s backoff (exponential: 5 × 2^1 = 10s)

---

## Reading Audit Logs

```bash
# Stream today's audit log live
python -c "
import json, time
from pathlib import Path
import datetime

log = Path(f'vault/Logs/AUDIT_{datetime.date.today()}.jsonl')
pos = log.stat().st_size if log.exists() else 0

while True:
    if log.exists() and log.stat().st_size > pos:
        with open(log) as f:
            f.seek(pos)
            for line in f:
                entry = json.loads(line)
                print(f'[{entry[\"severity\"]:8}] {entry[\"component\"]:25} {entry[\"event\"]}')
            pos = f.tell()
    time.sleep(1)
"

# Query for errors only
python -c "
import json
from pathlib import Path
import datetime
today = datetime.date.today().isoformat()
audit = Path(f'vault/Logs/AUDIT_{today}.jsonl')
if audit.exists():
    errors = [json.loads(l) for l in audit.read_text().splitlines()
              if l and json.loads(l).get('severity') in ('ERROR', 'CRITICAL')]
    print(f'{len(errors)} errors today:')
    for e in errors:
        print(f'  {e[\"ts\"]} [{e[\"component\"]}] {e[\"event\"]}: {e.get(\"error\", \"\")}')
"

# Component health snapshot
python -c "
import json
from pathlib import Path
h = Path('vault/Logs/HEALTH.json')
if h.exists():
    for comp, info in json.loads(h.read_text()).items():
        print(f'{comp:30} last_seen={info[\"last_seen\"]} errors={info[\"errors_total\"]}')
"
```

---

## Full System Smoke Test (30-second check)

```bash
python -c "
from pathlib import Path, PurePath
import json, datetime, sys

checks = []

# 1. Module imports
try:
    from audit_logger import AuditLogger
    from retry_handler import CircuitBreaker, retry
    from offline_queue import get_queue
    checks.append(('Modules import', True, ''))
except Exception as e:
    checks.append(('Modules import', False, str(e)))

# 2. Vault directories
for d in ['Needs_Action', 'Plans', 'Logs', 'Queue', 'Odoo_Drafts']:
    exists = (Path('vault') / d).exists()
    checks.append((f'vault/{d}/', exists, '' if exists else 'missing'))

# 3. Today audit log
today = datetime.date.today().isoformat()
audit = Path(f'vault/Logs/AUDIT_{today}.jsonl')
checks.append(('Audit log today', audit.exists(), '' if audit.exists() else 'not written yet'))

# 4. HEALTH.json
health = Path('vault/Logs/HEALTH.json')
checks.append(('HEALTH.json', health.exists(), '' if health.exists() else 'no component has logged yet'))

# 5. Offline queue clean
from offline_queue import get_queue
depth = get_queue('odoo').pending_count()
checks.append(('Odoo queue empty', depth == 0, f'{depth} items pending' if depth else ''))

# Print results
print()
print('=' * 55)
print('  Gold Tier Smoke Test')
print('=' * 55)
for name, passed, note in checks:
    status = 'PASS' if passed else 'FAIL'
    suffix = f'  ({note})' if note else ''
    print(f'  [{status}] {name}{suffix}')
print('=' * 55)
fails = sum(1 for _, p, _ in checks if not p)
print(f'  {len(checks) - fails}/{len(checks)} checks passed')
print()
sys.exit(1 if fails else 0)
"
```
