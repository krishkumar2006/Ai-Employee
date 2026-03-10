# End-to-End Test Guide — Platinum Tier

Full test coverage for the cloud/local split architecture. Run scenarios in order
— each builds on the previous. Gold Tier smoke test must pass first.

**Reference architecture:**
- Cloud VM: gmail_watcher, social_drafter, claim_agent, ralph_loop, vault_sync.sh
- Local Windows: orchestrator, approval_watcher, twitter_poster, meta_poster,
  linkedin_poster, whatsapp_watcher, odoo_mcp
- Sync bridge: GitHub (git push from cloud, git pull on local)

---

## Prerequisites

```bash
# 1. Gold Tier smoke test passes
python -c "
import sys; sys.path.insert(0, '.')
from audit_logger import AuditLogger
from retry_handler import CircuitBreaker
from offline_queue import get_queue
print('Gold Tier imports: OK')
"

# 2. Platinum modules import cleanly
python -c "
import sys; sys.path.insert(0, '.')
mods = ['config', 'rate_limiter']
for m in mods:
    try:
        __import__(m)
        print(f'OK  {m}')
    except Exception as e:
        print(f'FAIL {m}: {e}')
"

# 3. Vault structure exists (domain subfolders)
python -c "
from pathlib import Path
required = [
    'vault/Needs_Action/email',
    'vault/Needs_Action/social',
    'vault/Needs_Action/odoo',
    'vault/Pending_Approval/social',
    'vault/Pending_Approval/email',
    'vault/Done/email',
    'vault/Twitter_Drafts',
    'vault/Meta_Drafts',
    'vault/LinkedIn_Drafts',
]
missing = [d for d in required if not Path(d).exists()]
if missing:
    print('MISSING directories:', missing)
    print('Fix: python scripts/setup_vault_structure.py')
else:
    print('Vault structure: OK')
"

# 4. Deployment mode is set
python -c "
from config import cfg
print('Deployment mode:', cfg.mode)
print('Dry run:', cfg.is_dry_run())
print('Allowed actions sample:')
for act in ['email_read', 'social_draft', 'odoo_write']:
    try:
        cfg.assert_allowed(act)
        print(f'  {act}: allowed')
    except Exception as e:
        print(f'  {act}: BLOCKED ({e})')
"
```

---

## Test P-01: Deployment Mode Guard

**What it validates:** `config.py` — cloud/local action blocking, `ModeError` raised
correctly, DRY_RUN simulation.

### Step 1a: Cloud blocks write actions

```bash
python -c "
import os; os.environ['DEPLOYMENT_MODE'] = 'cloud'

# Force reload config with cloud mode
import importlib, config
config._instance = None
importlib.reload(config)
from config import cfg, ModeError

blocked = ['odoo_write', 'odoo_confirm', 'email_send',
           'social_post_twitter', 'approval_execute']
allowed = ['email_read', 'social_draft', 'odoo_read', 'plan_generate']

print('--- Cloud blocking test ---')
for action in blocked:
    try:
        cfg.assert_allowed(action, 'test')
        print(f'  FAIL (not blocked): {action}')
    except ModeError:
        print(f'  OK   BLOCKED: {action}')

print()
for action in allowed:
    try:
        cfg.assert_allowed(action, 'test')
        print(f'  OK   ALLOWED: {action}')
    except ModeError as e:
        print(f'  FAIL (wrongly blocked): {action} — {e}')
"
```

**Pass criteria:** All blocked actions raise `ModeError`. All allowed actions pass.

### Step 1b: DRY_RUN mode

```bash
python -c "
import os
os.environ['DEPLOYMENT_MODE'] = 'local'
os.environ['DRY_RUN'] = 'true'

import importlib, config
config._instance = None
importlib.reload(config)
from config import cfg

print('is_dry_run():', cfg.is_dry_run())
skipped = cfg.dry_run_guard('send invoice PKR 45000', 'odoo_mcp')
print('dry_run_guard returned (should be True):', skipped)
" 2>&1
```

**Pass criteria:** `is_dry_run()` returns `True`. `dry_run_guard` prints `[DRY RUN]` and returns `True`.

---

## Test P-02: Rate Limiter

**What it validates:** `rate_limiter.py` — sliding window enforcement, `RateLimitError`,
reset command, `--status` table.

### Step 2a: Enforce a limit

```bash
python -c "
from rate_limiter import RateLimiter, RateLimitError
import os

# Temporary low limit for testing
os.environ['RATE_LIMIT_SOCIAL_POST'] = '3'

from importlib import reload
import rate_limiter
reload(rate_limiter)
from rate_limiter import limiter

print('Consuming 3 social_post slots...')
for i in range(3):
    limiter.check_and_record('social_post')
    print(f'  call {i+1}: OK')

print('Attempting 4th call (should raise RateLimitError)...')
try:
    limiter.check_and_record('social_post')
    print('  FAIL — should have raised RateLimitError')
except RateLimitError as e:
    print(f'  OK   RateLimitError: {e}')
"
```

### Step 2b: Status table

```bash
python rate_limiter.py --status
```

**Expected:** Table showing `social_post` at or near limit.

### Step 2c: Reset

```bash
python rate_limiter.py --reset social_post
python rate_limiter.py --status
```

**Expected:** `social_post` count resets to 0.

**Pass criteria:** Limit enforced on 4th call. Status table readable. Reset works.

---

## Test P-03: Vault Structure + Claim Agent

**What it validates:** `scripts/setup_vault_structure.py` + `scripts/claim_agent.py`
atomic rename + stale recovery.

### Step 3a: Create test task

```bash
python -c "
from pathlib import Path
from datetime import datetime, timezone, timedelta
PKT = timezone(timedelta(hours=5))
now = datetime.now(tz=PKT)
task = '''---
type: email_reply
subject: Test claim scenario
priority: medium
domain: email
status: pending
ts: {ts}
---
This is a test task for claim_agent.py validation.
'''.format(ts=now.isoformat())
p = Path('vault/Needs_Action/email') / f'EMAIL_test_claim_{now.strftime(\"%H%M%S\")}.md'
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(task, encoding='utf-8')
print('Task created:', p.name)
"
```

### Step 3b: Run claim agent once

```bash
python scripts/claim_agent.py --agent orchestrator --domain email --poll 1 &
sleep 3
kill %1
```

### Step 3c: Verify claim

```bash
python -c "
from pathlib import Path
claimed = list(Path('vault/In_Progress/orchestrator').glob('EMAIL_test_claim_*.md'))
if claimed:
    print('CLAIMED:', claimed[0].name)
    text = claimed[0].read_text()
    print('Content preview:', text[:100])
else:
    print('FAIL — no claimed file found')
"
```

**Pass criteria:** Task moved from `Needs_Action/email/` to `In_Progress/orchestrator/`.

---

## Test P-04: Social Draft Pipeline (Cloud Side)

**What it validates:** `watchers/social_drafter.py` — reads social task, calls Claude,
writes platform-specific drafts, moves task to Done.

> Run this test on the cloud VM with `DEPLOYMENT_MODE=cloud` or simulate locally
> by setting the env var temporarily.

### Step 4a: Create a social task

```bash
python -c "
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
PKT = timezone(timedelta(hours=5))
now = datetime.now(tz=PKT)
task = {
    'type': 'social_request',
    'topic': 'How AI is transforming small business productivity in 2026',
    'platform': 'all',
    'tone': 'professional',
    'domain': 'social',
    'ts': now.isoformat()
}
Path('vault/Needs_Action/social').mkdir(parents=True, exist_ok=True)
p = Path('vault/Needs_Action/social') / f'SOCIAL_test_{now.strftime(\"%H%M%S\")}.json'
p.write_text(json.dumps(task, indent=2), encoding='utf-8')
print('Social task created:', p.name)
"
```

### Step 4b: Run drafter once

```bash
python watchers/social_drafter.py --once
```

### Step 4c: Verify drafts

```bash
python -c "
from pathlib import Path
for draft_dir in ['vault/Twitter_Drafts', 'vault/Meta_Drafts', 'vault/LinkedIn_Drafts']:
    drafts = sorted(Path(draft_dir).glob('DRAFT_*.md'))
    if drafts:
        latest = drafts[-1]
        text = latest.read_text(encoding='utf-8')
        status = 'draft' if 'status: draft' in text else '?'
        print(f'OK  {draft_dir}: {latest.name}  (status={status}, {len(text)} chars)')
    else:
        print(f'MISSING  {draft_dir}: no drafts found')

# Original task moved to Done?
done = list(Path('vault/Done/social').glob('SOCIAL_test_*.json'))
print('Task moved to Done/social:', bool(done))
"
```

**Pass criteria:** One draft in each of Twitter_Drafts, Meta_Drafts, LinkedIn_Drafts.
All have `status: draft`. Source task moved to `Done/social/`.

---

## Test P-05: Approval Watcher — Claim, Auto-Approve, Human Commands

**What it validates:** `watchers/approval_watcher.py` — claim-by-rename, auto-approve
threshold, human approve/reject CLI, DRY_RUN guard.

### Step 5a: Create a draft awaiting approval

```bash
python -c "
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
PKT = timezone(timedelta(hours=5))
now = datetime.now(tz=PKT)
draft = {
    'type': 'twitter_draft',
    'status': 'draft',
    'priority': 'low',
    'topic': 'AI productivity tips',
    'summary': 'Thread about AI boosting business productivity',
    'generated_at': now.isoformat(),
    'body': 'Thread: 5 ways AI saves 3+ hours daily...'
}
Path('vault/Pending_Approval/social').mkdir(parents=True, exist_ok=True)
fname = f'DRAFT_TWITTER_test_{now.strftime(\"%H%M%S\")}.json'
p = Path('vault/Pending_Approval/social') / fname
p.write_text(json.dumps(draft, indent=2), encoding='utf-8')
print('Approval draft created:', fname)
print('File:', p)
"
```

### Step 5b: List pending

```bash
python watchers/approval_watcher.py --list
```

**Expected:** File appears with `domain=social  priority=low`.

### Step 5c: Scan once (no auto-approve — threshold=none)

```bash
AUTO_APPROVE_BELOW=none python watchers/approval_watcher.py --once --verbose
```

**Expected:** File claimed to `In_Progress/approval_watcher/`, status `AWAITING HUMAN`.

### Step 5d: Human approve

```bash
# Get the filename from In_Progress
python -c "
from pathlib import Path
files = list(Path('vault/In_Progress/approval_watcher').glob('DRAFT_TWITTER_test_*.json'))
if files:
    print(files[0].name)
else:
    print('no file — check claim step')
"
# Copy the filename, then:
python watchers/approval_watcher.py --approve DRAFT_TWITTER_test_HHMMSS.json
```

### Step 5e: Verify approved

```bash
python -c "
from pathlib import Path
approved = list(Path('vault/Approved').rglob('DRAFT_TWITTER_test_*.json'))
print('Approved:', [f.name for f in approved])
"
```

**Pass criteria:** File in `vault/Approved/social/`. Audit log shows `action: human_approved`.

### Step 5f: Test auto-approve (threshold = low)

Repeat Step 5a with a new file, then:

```bash
AUTO_APPROVE_BELOW=low python watchers/approval_watcher.py --once --verbose
```

**Expected:** Low-priority draft auto-approved without human command.

---

## Test P-06: Social Poster (Local Only — Playwright)

**What it validates:** `watchers/twitter_poster.py` (or meta/linkedin) — picks up
`status: ready` drafts, posts via Playwright, moves to Posted.

> This test requires saved Playwright session cookies. Do NOT run on cloud.

### Step 6a: Create a ready-to-post draft

```bash
python -c "
from pathlib import Path
from datetime import datetime, timezone, timedelta
PKT = timezone(timedelta(hours=5))
now = datetime.now(tz=PKT)
draft = '''---
type: twitter_draft
post_type: tweet
status: ready
tone: professional
topic: Platinum Tier test
generated_at: {ts}
---

Testing the Personal AI Employee Platinum Tier.
Autonomous cloud → vault → local → Playwright pipeline. No paid APIs.

#AI #Automation #PersonalAIEmployee
'''.format(ts=now.isoformat())
Path('vault/Twitter_Drafts').mkdir(parents=True, exist_ok=True)
fname = f'DRAFT_TWITTER_platinum_test_{now.strftime(\"%H%M%S\")}.md'
(Path('vault/Twitter_Drafts') / fname).write_text(draft, encoding='utf-8')
print('Draft created:', fname)
print('Set status: ready to trigger poster')
"
```

### Step 6b: Run poster (DRY_RUN first)

```bash
DRY_RUN=true python watchers/twitter_poster.py --once
```

**Expected:** `[DRY RUN] Would post: ...` output. No actual post.

### Step 6c: Run for real

```bash
# Only if session cookie is saved and account is ready
python watchers/twitter_poster.py --once
```

### Step 6d: Verify

```bash
python -c "
from pathlib import Path
posted = list(Path('vault').rglob('*platinum_test*'))
for f in posted:
    text = f.read_text(encoding='utf-8')
    status = 'unknown'
    for line in text.splitlines():
        if line.startswith('status:'):
            status = line.split(':', 1)[1].strip()
    print(f'{f.parent.name}/{f.name}  status={status}')
"
```

**Pass criteria:** DRY_RUN prints without posting. Live run moves draft to
`vault/Twitter_Posted/` or updates `status: posted`.

---

## Test P-07: Vault Sync — Push/Pull Cycle

**What it validates:** `scripts/vault_sync.sh` (cloud) + `scripts/vault_sync_windows.py`
(local) — git push from cloud, git pull on local, conflict resolution for Dashboard.md.

### Step 7a: Simulate cloud push (run on VM or locally in cloud-mode)

```bash
# Create a file that simulates cloud output
python -c "
from pathlib import Path
from datetime import datetime, timezone, timedelta
PKT = timezone(timedelta(hours=5))
now = datetime.now(tz=PKT)
Path('vault/Needs_Action/email').mkdir(parents=True, exist_ok=True)
p = Path('vault/Needs_Action/email') / f'EMAIL_sync_test_{now.strftime(\"%H%M%S\")}.md'
p.write_text('---\ntype: sync_test\nstatus: pending\nts: {ts}\n---\nSync test\n'.format(ts=now.isoformat()))
print('Cloud file created:', p.name)
"

# On Linux/VM:
bash scripts/vault_sync.sh --dry-run   # preview
bash scripts/vault_sync.sh             # actual push
```

### Step 7b: Simulate local pull

```bash
# On Windows local:
python scripts/vault_sync_windows.py --once

# Or manually:
git pull --rebase
```

### Step 7c: Verify file arrived

```bash
python -c "
from pathlib import Path
files = list(Path('vault/Needs_Action/email').glob('EMAIL_sync_test_*.md'))
print('Synced files:', [f.name for f in files])
"
```

### Step 7d: Dashboard.md conflict test

```bash
# Simulate a conflict: edit Dashboard.md on both sides
# Then run sync — local side should win
python -c "
from pathlib import Path
d = Path('vault/Dashboard.md')
current = d.read_text(encoding='utf-8') if d.exists() else ''
d.write_text(current + '\n<!-- local addition -->\n', encoding='utf-8')
print('Local addition made')
"

python scripts/vault_sync_windows.py --once
# Or: git pull --rebase
# Dashboard.md local changes should survive (LOCAL WINS strategy)
```

**Pass criteria:** Files created on cloud side appear on local after sync.
Dashboard.md local edits preserved after pull (no overwrite).

---

## Test P-08: Odoo Docker Health

**What it validates:** `scripts/odoo_health.py` — 6 health checks, HEALTH_ODOO.json,
Telegram alert (if configured).

```bash
python scripts/odoo_health.py
```

**Expected output:**

```
[odoo_health] Running 6 checks...
  [PASS] HTTP :443 → 200
  [PASS] JSONRPC authenticate → uid=2
  [PASS] Docker containers: odoo, postgres, nginx — all running
  [PASS] Disk usage: 34% (threshold 85%)
  [PASS] SSL cert expires in 87 days
  [PASS] Last backup: 6 hours ago
[odoo_health] Status: HEALTHY (6/6)
  → vault/Logs/HEALTH_ODOO.json updated
```

```bash
python -c "
import json
from pathlib import Path
h = Path('vault/Logs/HEALTH_ODOO.json')
if h.exists():
    data = json.loads(h.read_text())
    print('Overall status:', data.get('status'))
    for check, result in data.get('checks', {}).items():
        icon = 'PASS' if result.get('ok') else 'FAIL'
        print(f'  [{icon}] {check}: {result.get(\"detail\", \"\")}')
else:
    print('HEALTH_ODOO.json not found — run odoo_health.py first')
"
```

**Pass criteria:** 5+ checks pass. `vault/Logs/HEALTH_ODOO.json` written with
`status: HEALTHY` or `status: DEGRADED` (not ERROR).

---

## Test P-09: Full E2E — Email → Cloud Draft → Local Approve → Execute

**The flagship scenario. Validates the entire Platinum pipeline end-to-end.**

This test simulates the complete flow manually (no real Gmail account required).

### Step 9a: Drop an email task card (simulates gmail_watcher output)

```bash
python -c "
from pathlib import Path
from datetime import datetime, timezone, timedelta
PKT = timezone(timedelta(hours=5))
now = datetime.now(tz=PKT)

task = '''---
type: email
subject: Request for Q1 proposal — consulting services
from: client@example.com
priority: high
domain: email
status: pending
ts: {ts}
source: gmail
message_id: PLAT_TEST_001
---

Hi,

We are interested in your AI consulting services for Q1 2026.
Could you please send us a proposal for automating our billing workflow?

Budget: PKR 500,000
Timeline: 6 weeks

Best regards,
Test Client
'''.format(ts=now.isoformat())

p = Path('vault/Needs_Action/email') / f'EMAIL_proposal_request_{now.strftime(\"%H%M%S\")}.md'
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(task, encoding='utf-8')
print('Email task created:', p.name)
print('Next: run ralph_loop or social_drafter to process it')
"
```

### Step 9b: Process with Ralph Loop (simulates cloud AI processing)

```bash
python ralph_loop.py \
  --task "Read vault/Needs_Action/email/EMAIL_proposal_request_*.md and generate a professional reply proposal. Save draft to vault/Pending_Approval/email/ with status: draft. Then write vault/Plans/E2E_TEST_COMPLETE.md as signal." \
  --done-type signal_file \
  --done-glob "vault/Plans/E2E_TEST_COMPLETE.md" \
  --label e2e-test \
  --max-iter 5
```

### Step 9c: Verify draft in Pending_Approval

```bash
python -c "
from pathlib import Path
drafts = list(Path('vault/Pending_Approval/email').glob('*.md'))
if drafts:
    print('Drafts awaiting approval:')
    for d in drafts:
        text = d.read_text(encoding='utf-8')
        status = next((l.split(':',1)[1].strip() for l in text.splitlines() if l.startswith('status:')), '?')
        print(f'  {d.name}  (status={status})')
else:
    print('No drafts yet — check logs/ralph_e2e-test.log')
"
```

### Step 9d: Human approval

```bash
# List and approve
python watchers/approval_watcher.py --list
python watchers/approval_watcher.py --approve EMAIL_proposal_reply_HHMMSS.md
```

### Step 9e: Verify end state

```bash
python -c "
import json
from pathlib import Path
import datetime

today = datetime.date.today().isoformat()
print()
print('=' * 55)
print('  Platinum E2E Test — Final State Check')
print('=' * 55)

checks = []

# Draft approved
approved = list(Path('vault/Approved').rglob('EMAIL_proposal_*.md'))
checks.append(('Draft approved', bool(approved),
               approved[0].name if approved else 'not found'))

# Signal file written by ralph_loop
signal = Path('vault/Plans/E2E_TEST_COMPLETE.md')
checks.append(('Ralph Loop signal', signal.exists(), ''))

# Audit log has the events
audit = Path(f'vault/Logs/AUDIT_{today}.jsonl')
if audit.exists():
    lines = [json.loads(l) for l in audit.read_text().splitlines() if l.strip()]
    approved_ev = [l for l in lines if l.get('action') == 'human_approved']
    plan_ev     = [l for l in lines if l.get('event')  == 'plan_generated']
    checks.append(('Audit: human_approved', bool(approved_ev), ''))
    checks.append(('Audit: plan_generated', bool(plan_ev), ''))
else:
    checks.append(('Audit log exists', False, 'no log today'))

for name, passed, note in checks:
    status = 'PASS' if passed else 'FAIL'
    suffix = f'  ({note})' if note else ''
    print(f'  [{status}] {name}{suffix}')

print('=' * 55)
fails = sum(1 for _, p, _ in checks if not p)
print(f'  {len(checks)-fails}/{len(checks)} checks passed')
"
```

**Pass criteria:** All 5 checks PASS. The email task went from Needs_Action →
ralph_loop processing → Pending_Approval draft → human approved → Approved folder.

---

## Test P-10: Platinum Smoke Test (30-second check)

Run this before any submission or deployment:

```bash
python -c "
import sys, json, datetime
sys.path.insert(0, '.')
from pathlib import Path

checks = []
today = datetime.date.today().isoformat()

# Module imports
try:
    from audit_logger import AuditLogger
    from retry_handler import CircuitBreaker
    from offline_queue import get_queue
    from config import cfg
    from rate_limiter import limiter
    checks.append(('Platinum module imports', True, ''))
except Exception as e:
    checks.append(('Platinum module imports', False, str(e)))

# Deployment mode
try:
    from config import cfg
    checks.append(('config.py loads', True, f'mode={cfg.mode}'))
except Exception as e:
    checks.append(('config.py loads', False, str(e)))

# Vault domain structure
for domain in ['email', 'social', 'odoo', 'general']:
    na = Path(f'vault/Needs_Action/{domain}')
    checks.append((f'vault/Needs_Action/{domain}', na.exists(), ''))

for folder in ['vault/Pending_Approval', 'vault/Twitter_Drafts',
               'vault/Meta_Drafts', 'vault/LinkedIn_Drafts']:
    exists = Path(folder).exists()
    checks.append((folder, exists, 'missing' if not exists else ''))

# Key Platinum scripts
for script in ['scripts/claim_agent.py', 'scripts/vault_sync.sh',
               'scripts/vault_sync_windows.py', 'scripts/odoo_health.py',
               'watchers/social_drafter.py', 'watchers/approval_watcher.py',
               'watchers/twitter_poster.py', 'watchers/meta_poster.py',
               'watchers/linkedin_poster.py', 'config.py', 'rate_limiter.py']:
    exists = Path(script).exists()
    checks.append((script, exists, 'missing' if not exists else ''))

# Rate limiter operational
try:
    from rate_limiter import limiter, RateLimitError
    checks.append(('rate_limiter operational', True, ''))
except Exception as e:
    checks.append(('rate_limiter operational', False, str(e)))

# cloud/local mode blocking
try:
    from config import cfg, ModeError
    if cfg.mode == 'cloud':
        try:
            cfg.assert_allowed('odoo_write', 'test')
            checks.append(('Cloud blocks odoo_write', False, 'NOT blocked'))
        except ModeError:
            checks.append(('Cloud blocks odoo_write', True, ''))
    else:
        checks.append(('Cloud block (local mode)', True, 'local mode — skip cloud check'))
except Exception as e:
    checks.append(('ModeError guard', False, str(e)))

# Ecosystem configs
for cfg_file in ['ecosystem.cloud.config.js', 'ecosystem.local.config.js']:
    exists = Path(cfg_file).exists()
    checks.append((cfg_file, exists, 'missing' if not exists else ''))

# Docker compose
docker_ok = Path('docker/docker-compose.yaml').exists()
checks.append(('docker/docker-compose.yaml', docker_ok, ''))

print()
print('=' * 65)
print('  Platinum Tier Smoke Test')
print('=' * 65)
for name, passed, note in checks:
    status = 'PASS' if passed else 'FAIL'
    suffix = f'  ({note})' if note else ''
    print(f'  [{status}] {name}{suffix}')
print('=' * 65)
fails = sum(1 for _, p, _ in checks if not p)
print(f'  {len(checks)-fails}/{len(checks)} checks passed')
print()
sys.exit(1 if fails else 0)
"
```

**Pass criterion:** All checks PASS, exit code 0.

---

## Reading Platinum Audit Logs

```bash
# Show today's events filtered by Platinum components
python -c "
import json, datetime
from pathlib import Path

today = datetime.date.today().isoformat()
log = Path(f'vault/Logs/AUDIT_{today}.jsonl')
if not log.exists():
    print('No audit log today')
else:
    PLAT = {'social_drafter','approval_watcher','claim_agent',
            'rate_limiter','config','vault_sync'}
    lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    plat_lines = [l for l in lines if l.get('component','') in PLAT]
    print(f'{len(plat_lines)} Platinum events today:')
    for e in plat_lines[-20:]:
        sev = e.get('severity','INFO')[:4]
        comp = e.get('component','?')[:20]
        ev = e.get('event','?')
        detail = e.get('action', e.get('error', ''))[:40]
        print(f'  [{sev}] {comp:20} {ev:25} {detail}')
"

# Show rate limiter current state
python rate_limiter.py --status

# Show approval queue
python watchers/approval_watcher.py --list
```
