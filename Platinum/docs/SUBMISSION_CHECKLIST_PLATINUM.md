# Hackathon Submission Checklist — Platinum Tier

Work through every section before submitting. Includes security disclosure template
required by most hackathons when submitting systems that interact with external accounts.

---

## Part 1: Code Quality

### 1.1 All Python files parse without syntax errors

```bash
python -c "
import ast
from pathlib import Path

files = [f for f in Path('.').rglob('*.py')
         if 'node_modules' not in str(f) and '.venv' not in str(f)]

errors = []
for f in files:
    try:
        ast.parse(f.read_text(encoding='utf-8', errors='ignore'))
    except SyntaxError as e:
        errors.append(f'{f}: line {e.lineno} — {e.msg}')

if errors:
    print(f'SYNTAX ERRORS ({len(errors)}):')
    for e in errors: print(f'  {e}')
else:
    print(f'OK — {len(files)} files parsed cleanly')
"
```

**Pass:** `OK — N files parsed cleanly`

---

### 1.2 All Platinum modules import cleanly

```bash
python -c "
import sys; sys.path.insert(0, '.')
failures = []
modules = [
    'audit_logger', 'retry_handler', 'offline_queue',   # Gold
    'config', 'rate_limiter',                            # Platinum
]
for mod in modules:
    try:
        __import__(mod)
        print(f'OK  {mod}')
    except Exception as e:
        print(f'FAIL {mod}: {e}')
        failures.append(mod)
if failures:
    import sys; sys.exit(1)
"
```

**Pass:** All modules print `OK`

---

### 1.3 No credentials or secrets in source files

```bash
python -c "
import re
from pathlib import Path

PATTERNS = [
    (r'password\s*=\s*[\"'][^\"']{4,}[\"']',       'hardcoded password'),
    (r'api_key\s*=\s*[\"'][^\"']{8,}[\"']',         'hardcoded API key'),
    (r'sk-ant-[a-zA-Z0-9\-_]{20,}',                 'Anthropic API key'),
    (r'AIza[0-9A-Za-z\-_]{35}',                     'Google API key'),
    (r'EAAB[a-zA-Z0-9]+',                            'Meta access token'),
    (r'[0-9]{15,18}-[a-zA-Z0-9_]{20,}',             'Twitter bearer-like token'),
    (r'postgres://[^:]+:[^@]+@',                     'Postgres connection string with password'),
]

files = [f for f in Path('.').rglob('*.py')
         if 'node_modules' not in str(f) and '.env' not in f.name]
files += [f for f in Path('.').rglob('*.js')
          if 'node_modules' not in str(f)]
files += [f for f in Path('.').rglob('*.json')
          if 'node_modules' not in str(f) and 'gmail_token' not in f.name]

hits = []
for f in files:
    try:
        text = f.read_text(encoding='utf-8', errors='ignore')
        for pat, label in PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                hits.append(f'{f}: {label}')
    except Exception:
        pass

if hits:
    print('POSSIBLE SECRETS:')
    for h in hits: print(f'  {h}')
else:
    print('OK — no hardcoded secrets detected')
"
```

**Pass:** `OK — no hardcoded secrets detected`

---

### 1.4 `.gitignore` covers all sensitive files

```bash
python -c "
from pathlib import Path
content = Path('.gitignore').read_text() if Path('.gitignore').exists() else ''

required = [
    '.env.local',
    'credentials.json',
    'gmail_token.json',
    '.gmail_processed_ids',
    'vault/Logs/',
    'vault/Queue/',
    'vault/In_Progress/',
    'vault/Approved/',
    'vault/Rejected/',
    '.twitter_session',
    '.meta_session',
    '.linkedin_session',
    '.whatsapp_session',
    '__pycache__',
    'node_modules',
    '*.pyc',
    '.env.docker',
]

missing = [r for r in required if r not in content]
if missing:
    print('MISSING from .gitignore:', missing)
else:
    print('OK — .gitignore covers all sensitive patterns')
"
```

**Pass:** No missing entries

---

### 1.5 `.env.cloud` has no real secrets (safe to commit)

```bash
python -c "
import re
from pathlib import Path

env_cloud = Path('.env.cloud')
if not env_cloud.exists():
    print('MISSING .env.cloud')
    exit(1)

text = env_cloud.read_text()
suspicious = []

# These lines are OK (placeholder values):
ok_patterns = ['CHANGEME', 'your_', 'example', 'placeholder', 'sk-ant-CHANGEME']
# These look like real secrets:
real_secret = re.compile(r'(PASSWORD|API_KEY|SECRET)\s*=\s*.{10,}', re.IGNORECASE)

for line in text.splitlines():
    if real_secret.search(line):
        if not any(p in line for p in ok_patterns):
            suspicious.append(line)

if suspicious:
    print('WARNING — .env.cloud may contain real secrets:')
    for l in suspicious:
        # Mask the value
        key = l.split('=')[0]
        print(f'  {key}=***MASKED***')
    print('  Move real secrets to .env.local (gitignored)')
else:
    print('OK — .env.cloud contains only placeholders')
"
```

**Pass:** `OK — .env.cloud contains only placeholders`

---

## Part 2: Tier Declaration

### 2.1 README.md declares Platinum tier

```bash
python -c "
from pathlib import Path
readme = Path('README.md')
if not readme.exists():
    print('FAIL — README.md missing')
    exit(1)
text = readme.read_text()
checks = [
    ('Platinum', 'tier declaration'),
    ('Docker', 'Docker Odoo reference'),
    ('Playwright', 'Playwright social posting reference'),
    ('vault_sync', 'vault sync reference'),
    ('rate_limiter', 'rate limiter reference'),
    ('DEPLOYMENT_MODE', 'deployment mode reference'),
]
for keyword, desc in checks:
    if keyword in text:
        print(f'OK  {desc}')
    else:
        print(f'FAIL missing: {desc} ({keyword!r})')
"
```

---

### 2.2 ASCII architecture diagram present in README.md

```bash
python -c "
from pathlib import Path
text = Path('README.md').read_text()
has_diagram = ('ORACLE CLOUD' in text or 'Cloud VM' in text) and ('WINDOWS' in text or 'LOCAL' in text)
print('OK  Architecture diagram present' if has_diagram else 'FAIL  ASCII diagram missing in README.md')
"
```

---

### 2.3 File map / tier table in README

```bash
python -c "
from pathlib import Path
text = Path('README.md').read_text()
has_table = 'Plat' in text and 'Gold' in text and 'Slvr' in text
print('OK  Tier file map present' if has_table else 'FAIL  No tier breakdown in README.md')
"
```

---

## Part 3: Functional Verification

### 3.1 Platinum smoke test (all checks must pass)

```bash
python -c "
import sys, json, datetime
sys.path.insert(0, '.')
from pathlib import Path
checks = []
today = datetime.date.today().isoformat()

try:
    from audit_logger import AuditLogger
    from retry_handler import CircuitBreaker
    from offline_queue import get_queue
    from config import cfg
    from rate_limiter import limiter
    checks.append(('All Platinum imports', True, ''))
except Exception as e:
    checks.append(('All Platinum imports', False, str(e)))

for d in ['vault/Needs_Action/email','vault/Needs_Action/social',
          'vault/Pending_Approval','vault/Twitter_Drafts',
          'vault/Meta_Drafts','vault/LinkedIn_Drafts']:
    ok = Path(d).exists()
    checks.append((d, ok, 'missing' if not ok else ''))

for f in ['config.py','rate_limiter.py','scripts/claim_agent.py',
          'scripts/vault_sync.sh','scripts/vault_sync_windows.py',
          'scripts/odoo_health.py','watchers/social_drafter.py',
          'watchers/approval_watcher.py','watchers/twitter_poster.py',
          'watchers/meta_poster.py','watchers/linkedin_poster.py',
          'docker/docker-compose.yaml','ecosystem.cloud.config.js',
          'ecosystem.local.config.js','.env.cloud']:
    ok = Path(f).exists()
    checks.append((f, ok, 'missing' if not ok else ''))

print()
print('=' * 62)
print('  Pre-Submission Platinum Smoke Test')
print('=' * 62)
for name, passed, note in checks:
    s = 'PASS' if passed else 'FAIL'
    n = f'  ({note})' if note else ''
    print(f'  [{s}] {name}{n}')
print('=' * 62)
fails = sum(1 for _,p,_ in checks if not p)
print(f'  {len(checks)-fails}/{len(checks)} checks passed')
sys.exit(1 if fails else 0)
"
```

**Pass:** All PASS, exit code 0

---

### 3.2 Cloud mode blocks write actions

```bash
python -c "
import os; os.environ['DEPLOYMENT_MODE'] = 'cloud'
import importlib, config
config._instance = None
importlib.reload(config)
from config import cfg, ModeError

blocked_correctly = 0
for action in ['odoo_write','odoo_confirm','email_send','social_post_twitter','approval_execute']:
    try:
        cfg.assert_allowed(action, 'test')
        print(f'FAIL — {action} NOT blocked on cloud')
    except ModeError:
        blocked_correctly += 1

print(f'OK  {blocked_correctly}/5 write actions blocked on cloud')
"
```

**Pass:** `5/5 write actions blocked on cloud`

---

### 3.3 Rate limiter enforces limits

```bash
python -c "
import os; os.environ['RATE_LIMIT_SOCIAL_POST'] = '2'
import importlib, rate_limiter
importlib.reload(rate_limiter)
from rate_limiter import RateLimiter, RateLimitError
lim = RateLimiter()
for i in range(2):
    lim.check_and_record('social_post')
try:
    lim.check_and_record('social_post')
    print('FAIL — should have raised RateLimitError')
except RateLimitError:
    print('OK  Rate limiter enforced at limit=2')
"
```

**Pass:** `OK  Rate limiter enforced at limit=2`

---

### 3.4 DRY_RUN guard skips execution

```bash
python -c "
import os; os.environ['DRY_RUN'] = 'true'; os.environ['DEPLOYMENT_MODE'] = 'local'
import importlib, config; config._instance = None; importlib.reload(config)
from config import cfg
skipped = cfg.dry_run_guard('test action', 'test_component')
print('OK  DRY_RUN guard works' if skipped else 'FAIL  DRY_RUN guard returned False')
"
```

---

### 3.5 Vault claim-by-rename works

```bash
python -c "
from pathlib import Path
import os

# Create test task
src = Path('vault/Needs_Action/email/CHECKLIST_CLAIM_TEST.md')
src.parent.mkdir(parents=True, exist_ok=True)
src.write_text('---\ntype: test\n---\n')

dest_dir = Path('vault/In_Progress/orchestrator')
dest_dir.mkdir(parents=True, exist_ok=True)
dest = dest_dir / src.name

# Claim
src.rename(dest)
claimed = dest.exists() and not src.exists()
print('OK  Claim-by-rename atomic' if claimed else 'FAIL  Claim did not work')

# Cleanup
dest.unlink(missing_ok=True)
"
```

---

## Part 4: Repository Checklist

### 4.1 Repository structure

```
[ ] Repository is PUBLIC (or shared with judges)
[ ] README.md at root — Platinum tier declared, ASCII diagram, file map, quickstart
[ ] All Python source files committed (no .pyc, no __pycache__)
[ ] vault/ structure with .gitkeep files in empty folders
[ ] .gitignore in place and verified (Test 1.4 passes)
[ ] .env.cloud committed with placeholder values only
[ ] .env.local NOT committed
[ ] .env.docker NOT committed (only .env.docker.example)
[ ] credentials.json NOT committed
[ ] gmail_token.json NOT committed
[ ] Session files NOT committed (.twitter_session/, .meta_session/, etc.)
[ ] docker/ folder committed (compose, nginx config, odoo.conf, example env)
[ ] ecosystem.cloud.config.js and ecosystem.local.config.js committed
[ ] docs/ folder with all 8 documentation files
[ ] vault/SKILLS.md describes Platinum capabilities
[ ] vault/Business_Goals.md filled in (no real revenue numbers required)
[ ] At least one sample task card in vault/Needs_Action/email/ and /social/
[ ] At least one sample draft in vault/Twitter_Drafts/ or vault/LinkedIn_Drafts/
```

### 4.2 Verify nothing sensitive is staged

```bash
python -c "
import subprocess
result = subprocess.run(['git', 'status', '--short'], capture_output=True, text=True)
dangerous = [l for l in result.stdout.splitlines()
             if any(kw in l.lower() for kw in
                    ['credentials', 'gmail_token', '.env.local', '.env.docker',
                     '.twitter_session', '.meta_session', '.linkedin_session',
                     '.whatsapp_session', 'id_rsa', 'id_ed25519'])]
if dangerous:
    print('DANGER — sensitive files in git status:')
    for d in dangerous: print(f'  {d}')
else:
    print('OK — no sensitive files detected in git status')
"
```

### 4.3 Verify gitignored items are actually ignored

```bash
python -c "
import subprocess
ignored_dirs = [
    'vault/Logs/', 'vault/Queue/', 'vault/In_Progress/',
    'vault/Approved/', 'vault/Rejected/',
]
for d in ignored_dirs:
    r = subprocess.run(['git', 'check-ignore', '-q', d],
                       capture_output=True)
    if r.returncode == 0:
        print(f'OK   ignored: {d}')
    else:
        print(f'WARN not ignored: {d}  (add to .gitignore)')
"
```

---

## Part 5: Demo Video

### 5.1 Video requirements

| Requirement | Done? |
|-------------|-------|
| Length matches hackathon rules (usually 3–5 min) | [ ] |
| Shows system RUNNING — not slides | [ ] |
| Demonstrates the full E2E flow (email → cloud → approve → execute) | [ ] |
| Shows vault/ files created in real time (file browser open) | [ ] |
| Narration explains WHAT each step does and WHY | [ ] |
| Mentions tier explicitly ("Platinum because...") | [ ] |
| Shows DEPLOYMENT_MODE separation (cloud vs local) | [ ] |
| Shows rate limiter --status output | [ ] |
| Shows approval_watcher --list and --approve | [ ] |
| Shows DRY_RUN mode (optional — good for safety demo) | [ ] |
| 1080p minimum, audio clear | [ ] |

### 5.2 Suggested video script (5 minutes)

```
0:00–0:25  Architecture overview
           Show README ASCII diagram on screen
           "Two machines: cloud VM does the thinking, local does the acting"

0:25–1:15  Email → task card (simulated)
           Create a task in vault/Needs_Action/email/ manually
           Show file appear, show AUDIT_*.jsonl entry
           "gmail_watcher does this automatically — we're simulating it"

1:15–2:00  Cloud: social_drafter generates drafts
           python watchers/social_drafter.py --once
           Show drafts appearing in vault/Twitter_Drafts/
           Show draft content (status: draft)

2:00–2:40  Local: approval_watcher
           python watchers/approval_watcher.py --list
           python watchers/approval_watcher.py --approve DRAFT_xyz.md
           Show file move to vault/Approved/
           "Human stays in the loop — no autonomous posting"

2:40–3:10  Rate limiter and deployment mode
           python rate_limiter.py --status
           python -c "from config import cfg; print('mode:', cfg.mode)"
           "Cloud mode would block this action — demo ModeError"

3:10–3:45  Odoo HITL (if Odoo is running)
           Ask Claude to draft an invoice
           Show vault/Odoo_Drafts/ file
           Confirm via MCP
           Show invoice in Odoo UI

3:45–4:15  Vault sync demo
           "Cloud pushes every 5 minutes via git"
           Show git log --oneline from cloud perspective
           Show pull on local side
           "GitHub is the sync bridge — no paid queue"

4:15–5:00  System health + wrap-up
           python scripts/odoo_health.py  (or show HEALTH_ODOO.json)
           python watchdog.py --status or pm2 list
           Show AUDIT_*.jsonl populating
           "Full audit trail — every action logged"
           State tier: "This is Platinum: cloud/local split, Odoo Docker, Playwright social"
```

### 5.3 Recording tips

- Show both terminal AND vault file browser (Windows Explorer / Finder) side by side
- Pre-create 3–5 sample task cards before recording so there's content to process
- Keep a `demo_e2e.py` script ready to create tasks on demand without typing
- Record in `DEPLOYMENT_MODE=local` so all actions are permitted
- Have `python rate_limiter.py --status` output visible to show safety mechanisms

---

## Part 6: Security Disclosure

Most hackathons require disclosure when a submission interacts with external accounts,
financial systems, or user data. Complete this section in your submission form.

### 6.1 Security disclosure template

Copy and adapt this for your submission:

```
SECURITY DISCLOSURE — Personal AI Employee (Platinum Tier)

This system interacts with the following external services on behalf of the operator:

1. Gmail (Google OAuth 2.0)
   - Access: Read unread emails, send replies (with human approval)
   - Scope: gmail.readonly + gmail.send (optional)
   - Credentials: OAuth token stored locally in watchers/gmail_token.json (gitignored)
   - Human approval: All email sends require explicit --approve before execution

2. Odoo 17 ERP (self-hosted, operator-owned instance)
   - Access: Read/create invoices, payments, contacts, tasks
   - Authentication: Username/password via JSON-RPC (stored in .env.local, gitignored)
   - Human approval: All write operations (create/confirm) require HITL approval
   - Cloud mode: odoo_write and odoo_confirm are BLOCKED on the cloud VM

3. Twitter/X, Meta (Facebook/Instagram), LinkedIn
   - Access: Post content on behalf of the operator's personal/business accounts
   - Method: Playwright browser automation using saved session cookies (not API keys)
   - Credentials: Session cookies stored in gitignored .twitter_session/, .meta_session/,
     .linkedin_session/ directories
   - Human approval: Posts only execute after human sets status: ready in draft file
   - No API keys: No paid Twitter API v2, Meta Graph API, or LinkedIn API used

4. WhatsApp (operator's personal account)
   - Access: Read messages, send replies (with human approval)
   - Method: Playwright + WhatsApp Web (browser automation)
   - Credentials: Session stored in gitignored .whatsapp_session/

DATA HANDLING:
- No user data is sent to third-party services other than the intended platforms
- Vault files (task cards, drafts) are stored in a private GitHub repository
- Audit logs are local-only and never synced to cloud
- The operator's credentials never leave their own machines/VM

RATE LIMITING:
- All external actions are rate-limited per 24-hour sliding window
- Defaults: odoo_write=10/day, email_send=20/day, social_post=5/day/platform
- Rate limits are configurable and can be set to 0 to disable an action type entirely

DRY_RUN MODE:
- Setting DRY_RUN=true makes ALL actions no-ops (prints intent but does not execute)
- Suitable for demos and testing without touching any external accounts

HUMAN-IN-THE-LOOP:
- The cloud VM cannot post, send, or confirm anything — it can only draft
- Every action that touches an external account requires either:
  a) Explicit human command: python approval_watcher.py --approve FILE.md
  b) Auto-approve only for explicitly configured low-priority items (default: none)
```

### 6.2 Checklist: What your judges need to know

```
[ ] No paid APIs used — confirm in submission text
[ ] Gmail OAuth scopes are minimal (read + send, no delete/admin)
[ ] Social session cookies stored locally only (gitignored)
[ ] Financial data (Odoo) never sent to cloud
[ ] Human approval required before ANY external account action
[ ] DRY_RUN mode demonstrated in video
[ ] Rate limits demonstrated in video
[ ] .gitignore verified to exclude all credentials
[ ] .env.cloud contains NO real passwords
[ ] No user data other than operator's own accounts is processed
```

---

## Part 7: Final Submission

### 7.1 Submission links

```
[ ] GitHub repository URL:  ________________________________
[ ] Demo video URL:         ________________________________
[ ] Live demo URL:          ________________________________  (if applicable)
```

### 7.2 Submission description template

```
Personal AI Employee — Platinum Tier

An autonomous, self-healing AI business assistant split across two machines:
an Oracle Cloud VM (always-on intelligence) and a Windows local machine
(human approval + execution). Zero paid social media APIs. Every action
is logged, rate-limited, and requires human sign-off before touching any
external account.

TIER: Platinum

Platinum Tier features:
────────────────────────────────────────────────────────────
INFRASTRUCTURE
  • Oracle Cloud VM (free tier) + Windows local machine split
  • Docker Compose: Odoo 17 + PostgreSQL 15 + Nginx + Let's Encrypt SSL
  • PM2 process management: ecosystem.cloud.config.js + ecosystem.local.config.js
  • Vault sync: git push/pull every 5 min (GitHub as free sync bridge)

CLOUD-ONLY (read + draft + plan)
  • gmail_watcher: OAuth email → task cards + Claude plans
  • social_drafter: Claude → platform-specific drafts (Twitter, Meta, LinkedIn)
  • ralph_loop: autonomous multi-step inbox audit
  • claim_agent: atomic vault task claiming by filesystem rename

LOCAL-ONLY (human approval + execution)
  • approval_watcher: claim/approve/reject drafts via CLI
  • twitter_poster / meta_poster / linkedin_poster: Playwright (no API cost)
  • whatsapp_watcher: Playwright WhatsApp Web
  • Odoo MCP: HITL invoice + payment + CRM (11 tools, local confirm only)

CROSS-CUTTING
  • config.py: DEPLOYMENT_MODE + assert_allowed() + dry_run_guard()
  • rate_limiter.py: per-action sliding-window (file-backed, no Redis required)
  • audit_logger.py: immutable JSONL per day + HEALTH.json snapshots
  • retry_handler.py: @retry + 3-state CircuitBreaker per service
  • offline_queue.py: durable file queue + auto-drain on circuit close

SECURITY
  • 14 cloud-blocked actions enforced at runtime (ModeError)
  • DRY_RUN mode: all actions simulated without side effects
  • Rate limits: configurable per action, default 5/day social posts
  • Human-in-the-loop: no autonomous external execution

NO PAID APIs: Playwright replaces Twitter API ($100/mo), Meta Graph API
(business verification required), LinkedIn API (partner approval required).

GitHub: [link]
Demo video: [link]
```

### 7.3 Final 60-second check

```bash
python -c "
import sys, subprocess
from pathlib import Path
sys.path.insert(0, '.')

ok = True
print()
print('FINAL PRE-SUBMISSION CHECK')
print('=' * 45)

# README Platinum
readme = Path('README.md')
if readme.exists() and 'Platinum' in readme.read_text():
    print('OK  README.md — Platinum tier declared')
else:
    print('FAIL README.md missing or no Platinum mention'); ok = False

# All key files
for f in ['config.py','rate_limiter.py','scripts/claim_agent.py',
          'scripts/vault_sync.sh','scripts/vault_sync_windows.py',
          'watchers/social_drafter.py','watchers/approval_watcher.py',
          'watchers/twitter_poster.py','watchers/meta_poster.py',
          'watchers/linkedin_poster.py','docker/docker-compose.yaml',
          'ecosystem.cloud.config.js','ecosystem.local.config.js','.env.cloud']:
    if Path(f).exists():
        print(f'OK  {f}')
    else:
        print(f'FAIL {f} — MISSING'); ok = False

# No secrets staged
result = subprocess.run(['git','status','--short'], capture_output=True, text=True)
danger = [l for l in result.stdout.splitlines()
          if any(k in l.lower() for k in ['credentials','gmail_token','.env.local',
                                           '.env.docker','_session'])]
if danger:
    print('FAIL Sensitive files may be staged:')
    for d in danger: print(f'     {d}')
    ok = False
else:
    print('OK  No sensitive files staged')

# AuditLogger writes
from audit_logger import AuditLogger
import datetime
AuditLogger('final_check').info('submission_check', status='running')
today = datetime.date.today().isoformat()
if Path(f'vault/Logs/AUDIT_{today}.jsonl').exists():
    print('OK  AuditLogger writes successfully')
else:
    print('FAIL AuditLogger write failed'); ok = False

print('=' * 45)
print('READY TO SUBMIT' if ok else 'FIX ISSUES ABOVE FIRST')
sys.exit(0 if ok else 1)
"
```

**Pass criterion:** All OK, exit code 0, prints `READY TO SUBMIT`
