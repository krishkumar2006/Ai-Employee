# Hackathon Submission Checklist — Gold Tier

Work through this top-to-bottom before submitting. Each section has
a clear pass/fail criterion and the exact command to verify it.

---

## Part 1: Code Quality

### 1.1 All Python files parse without syntax errors

```bash
python -c "
import ast
from pathlib import Path

files = list(Path('.').rglob('*.py'))
files = [f for f in files if 'node_modules' not in str(f)]

errors = []
for f in files:
    try:
        ast.parse(f.read_text(encoding='utf-8'))
    except SyntaxError as e:
        errors.append(f'{f}: {e}')

if errors:
    print('SYNTAX ERRORS:')
    for e in errors: print(' ', e)
else:
    print(f'OK — {len(files)} files parsed cleanly')
"
```

**Pass criterion:** `OK — N files parsed cleanly`

---

### 1.2 Gold Tier modules import cleanly

```bash
python -c "
import sys
sys.path.insert(0, '.')
failures = []
for mod in ['audit_logger', 'retry_handler', 'offline_queue']:
    try:
        __import__(mod)
        print(f'OK  {mod}')
    except Exception as e:
        failures.append((mod, str(e)))
        print(f'FAIL {mod}: {e}')
if failures:
    import sys; sys.exit(1)
"
```

**Pass criterion:** All three modules print `OK`

---

### 1.3 No credentials hardcoded in source files

```bash
python -c "
import re
from pathlib import Path

# Patterns that suggest hardcoded secrets
PATTERNS = [
    r'password\s*=\s*[\"'][^\"']{4,}[\"']',
    r'api_key\s*=\s*[\"'][^\"']{8,}[\"']',
    r'sk-[a-zA-Z0-9]{20,}',
    r'AIza[0-9A-Za-z\-_]{35}',
]

files = [f for f in Path('.').rglob('*.py') if 'node_modules' not in str(f)]
hits = []
for f in files:
    text = f.read_text(encoding='utf-8', errors='ignore')
    for pat in PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            hits.append(f'{f.name}: matches {pat[:30]}...')

if hits:
    print('POSSIBLE SECRETS FOUND:')
    for h in hits: print(' ', h)
else:
    print('OK — no hardcoded credentials detected')
"
```

**Pass criterion:** `OK — no hardcoded credentials detected`
If hits appear, move secrets to `.env` or `.claude/mcp.json` (gitignored).

---

### 1.4 .gitignore covers all sensitive files

```bash
python -c "
from pathlib import Path
gitignore = Path('.gitignore')
content = gitignore.read_text() if gitignore.exists() else ''

required = [
    '.env',
    'credentials.json',
    'gmail_token.json',
    '__pycache__',
    'node_modules',
    '.gmail_processed_ids',
    '*.pyc',
]

missing = [r for r in required if r not in content]
if missing:
    print('MISSING from .gitignore:', missing)
else:
    print('OK — .gitignore covers all sensitive patterns')
"
```

**Pass criterion:** No missing entries

---

## Part 2: Tier Declaration

### 2.1 Declare your tier clearly in README.md

Check that `README.md` contains:
- [ ] The word "Gold" in the title or first paragraph
- [ ] Architecture diagram (Mermaid or ASCII)
- [ ] Table mapping files to tiers (Bronze / Silver / Gold)
- [ ] Quickstart instructions that actually work

**Manual check:** Read `README.md` first section. Is the tier obvious to a judge in 10 seconds?

---

### 2.2 Complete the tier declaration table in README

Ensure the Tier Roadmap / Breakdown table is accurate and all Gold rows say "Done".

---

### 2.3 SKILLS.md reflects actual capabilities

```bash
python -c "
from pathlib import Path
skills = Path('vault/SKILLS.md').read_text()
required_sections = ['Gold Tier', 'Odoo', 'CEO Briefing', 'Ralph', 'audit']
missing = [s for s in required_sections if s.lower() not in skills.lower()]
if missing:
    print('Missing sections in SKILLS.md:', missing)
else:
    print('OK — SKILLS.md covers all Gold Tier capabilities')
"
```

---

## Part 3: Functional Verification

### 3.1 Run the full smoke test

```bash
python -c "
from pathlib import Path, PurePath
import json, datetime, sys

checks = []

try:
    from audit_logger import AuditLogger
    from retry_handler import CircuitBreaker, retry
    from offline_queue import get_queue
    checks.append(('Module imports', True, ''))
except Exception as e:
    checks.append(('Module imports', False, str(e)))

for d in ['Needs_Action', 'Plans', 'Logs', 'Queue', 'Odoo_Drafts']:
    exists = (Path('vault') / d).exists()
    checks.append((f'vault/{d}/', exists, 'missing' if not exists else ''))

for f in ['watchdog.py', 'orchestrator.py', 'ralph_loop.py',
          'audit_logger.py', 'retry_handler.py', 'offline_queue.py']:
    exists = Path(f).exists()
    checks.append((f, exists, 'missing' if not exists else ''))

for f in ['mcp/odoo_mcp.py', 'watchers/filesystem_watcher.py',
          'watchers/gmail_watcher.py', 'watchers/ceo_briefing.py']:
    exists = Path(f).exists()
    checks.append((f, exists, 'missing' if not exists else ''))

print()
print('=' * 58)
print('  Pre-submission Smoke Test')
print('=' * 58)
for name, passed, note in checks:
    s = 'PASS' if passed else 'FAIL'
    n = f'  ({note})' if note else ''
    print(f'  [{s}] {name}{n}')
print('=' * 58)
fails = sum(1 for _, p, _ in checks if not p)
print(f'  {len(checks) - fails}/{len(checks)} checks passed')
sys.exit(1 if fails else 0)
"
```

**Pass criterion:** All checks PASS, exit code 0

---

### 3.2 File drop test (Bronze baseline)

```bash
# This must work — it's the foundation
echo "hackathon test file" > vault/Inbox/HACKATHON_TEST.txt
# Start filesystem_watcher in background, wait 3s, check result:
python -c "
import time, subprocess, sys
from pathlib import Path

proc = subprocess.Popen([sys.executable, 'watchers/filesystem_watcher.py'])
time.sleep(4)
proc.terminate()
cards = list(Path('vault/Needs_Action').glob('FILE_HACKATHON_TEST*'))
print('Task card created:', bool(cards))
if cards: print(' ', cards[0].name)
"
```

**Pass criterion:** Task card found

---

### 3.3 Manual CEO briefing test (skip Odoo)

```bash
python watchers/ceo_briefing.py --no-odoo --force
python -c "
import datetime
from pathlib import Path
today = datetime.date.today().isoformat()
f = Path(f'vault/Plans/CEO_BRIEFING_{today}.md')
print('CEO Briefing exists:', f.exists())
if f.exists():
    sections = [l for l in f.read_text().splitlines() if l.startswith('##')]
    print('Sections:', sections[:4], '...' if len(sections) > 4 else '')
"
```

**Pass criterion:** Briefing file exists with 4+ sections

---

## Part 4: GitHub Repository

### 4.1 Repository structure checklist

```
[ ] Repository is PUBLIC (not private)
[ ] README.md is at root — has Gold Tier title, diagram, quickstart
[ ] All Python source files committed
[ ] vault/ structure committed (empty folders with .gitkeep OK)
[ ] .gitignore in place
[ ] .env, credentials.json, gmail_token.json NOT committed
[ ] .claude/mcp.json committed with placeholder values (not real passwords)
[ ] docs/ folder committed with TEST_GUIDE, ERRORS_AND_FIXES, LESSONS_LEARNED
[ ] No large binaries committed (check with: git ls-files | grep -v .py | grep -v .md)
```

### 4.2 Verify gitignore is actually working

```bash
python -c "
import subprocess
result = subprocess.run(['git', 'status', '--short'], capture_output=True, text=True)
lines = result.stdout.splitlines()
dangerous = [l for l in lines if any(kw in l for kw in
    ['credentials', 'token.json', '.env', 'gmail_token'])]
if dangerous:
    print('DANGER — sensitive files NOT ignored:')
    for l in dangerous: print(' ', l)
else:
    print('OK — no sensitive files staged')
print()
print('Untracked / modified files:')
for l in lines[:20]: print(' ', l)
"
```

---

### 4.3 Repository includes a sample vault

Judges should be able to understand the system without running it.
Include sample files (anonymized/dummy data):

```
[ ] vault/Needs_Action/   — at least one sample EMAIL_*.md and FILE_*.md
[ ] vault/Plans/          — at least one sample DAILY_BRIEFING_*.md or PLAN_*.md
[ ] vault/Business_Goals.md   — filled in (no real revenue numbers needed)
[ ] vault/SKILLS.md           — complete
[ ] vault/Dashboard.md        — a sample screenshot-in-markdown
```

---

## Part 5: Demo Video

### 5.1 Video requirements

| Requirement | Done? |
|-------------|-------|
| Length: 3–5 minutes (check hackathon rules) | [ ] |
| Shows the system actually running (not slides) | [ ] |
| Demonstrates at least 3 Gold Tier features | [ ] |
| Audio narration explaining what each component does | [ ] |
| Shows vault/ files being created in real time | [ ] |
| Mentions tier explicitly ("This is Gold Tier because...") | [ ] |

### 5.2 Suggested video flow (4 minutes)

```
0:00 — 0:20  System overview (show README diagram on screen)
0:20 — 1:00  File drop demo: drop file → task card appears → plan generated
1:00 — 1:40  Email demo: show Gmail inbox → task card appears (priority: high)
1:40 — 2:30  Odoo HITL: Claude drafts invoice → show vault/Odoo_Drafts/ → confirm → Odoo invoice
2:30 — 3:00  Show vault/Logs/AUDIT_*.jsonl populating in real-time
3:00 — 3:30  Show watchdog restarting crashed orchestrator
3:30 — 4:00  Show CEO_BRIEFING_*.md with KPI scorecard
```

### 5.3 Screen recording tips

- Use 1080p minimum
- Show terminal output AND vault/ file browser side by side
- Pre-seed vault/Needs_Action/ with 3–5 cards so the audit demo doesn't need to wait
- Have Odoo open in a browser tab so you can show the invoice appearing live
- Record in PKT timezone so timestamps match your code comments

---

## Part 6: Final Submission Package

### 6.1 Submission links

```
[ ] GitHub repository URL: _______________________________
[ ] Demo video URL (YouTube / Loom / Google Drive): _______
[ ] Live demo URL (if applicable): _______________________
```

### 6.2 Submission text / description

Template (adapt to your hackathon's format):

```
Personal AI Employee — Gold Tier

A self-healing, autonomous AI system that acts as a personal business assistant.

TIER: Gold

Gold Tier features demonstrated:
- Orchestrator with 7 scheduled tasks (briefings, audits, cleanup)
- Ralph Loop: autonomous multi-step Claude processing (signal-file completion)
- Odoo 19 ERP integration via MCP: HITL invoice + payment workflows (11 tools)
- Weekly CEO Briefing: KPI audit + financial summary + Claude narrative
- AuditLogger: structured JSONL logs + HEALTH.json per-component snapshots
- retry_handler: @retry decorator + 3-state CircuitBreaker per service
- offline_queue: durable file-based queue for Odoo ops when ERP is down
- watchdog.py: process supervisor with exponential backoff restart
- Meta Graph API + Twitter v2 + LinkedIn social posting MCPs
- Graceful degradation: Odoo down → ops queued → auto-drained on recovery

GitHub: [link]
Demo: [link]
```

### 6.3 Final sanity check

Run this 60 seconds before submitting:

```bash
python -c "
print()
print('PRE-SUBMISSION FINAL CHECK')
print('=' * 40)

from pathlib import Path
import json, datetime, sys

ok = True

# README exists and mentions Gold
readme = Path('README.md')
if readme.exists() and 'Gold' in readme.read_text():
    print('OK  README.md — mentions Gold Tier')
else:
    print('FAIL README.md — missing or no Gold mention')
    ok = False

# All key files present
for f in ['watchdog.py', 'audit_logger.py', 'retry_handler.py',
          'offline_queue.py', 'orchestrator.py', 'ralph_loop.py',
          'mcp/odoo_mcp.py', 'watchers/ceo_briefing.py']:
    exists = Path(f).exists()
    status = 'OK  ' if exists else 'FAIL'
    if not exists: ok = False
    print(f'{status} {f}')

# No credentials in git
import subprocess
result = subprocess.run(['git', 'status', '--short'],
    capture_output=True, text=True, cwd='.')
dangerous = [l for l in result.stdout.splitlines()
    if any(kw in l for kw in ['credentials', 'gmail_token', '.env'])]
if dangerous:
    print('FAIL Sensitive files may be committed:', dangerous)
    ok = False
else:
    print('OK  No sensitive files in git status')

# Vault/Logs dir writable
from audit_logger import AuditLogger
log = AuditLogger('pre_submission_check')
log.info('submission_check', status='running')
today = datetime.date.today().isoformat()
audit = Path(f'vault/Logs/AUDIT_{today}.jsonl')
if audit.exists():
    print('OK  AuditLogger writes to vault/Logs/')
else:
    print('FAIL AuditLogger could not write')
    ok = False

print('=' * 40)
print('READY TO SUBMIT' if ok else 'FIX ISSUES ABOVE BEFORE SUBMITTING')
sys.exit(0 if ok else 1)
"
```

**Pass criterion:** All OK, exit code 0, prints `READY TO SUBMIT`
