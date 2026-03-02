# Common Platinum Tier Errors & Fixes

Grouped by subsystem. Every entry has: exact error or symptom → root cause → fix.
Gold Tier errors are in `docs/ERRORS_AND_FIXES.md`. This file covers Platinum-only issues.

---

## config.py / Deployment Mode

### P-01 — `ModeError: action 'odoo_write' is blocked in cloud mode`

**Where:** Any cloud-side component that tries to call a write action.

**Cause:** You set `DEPLOYMENT_MODE=cloud` but are calling an action that only
runs locally. This is intentional — the cloud guard is working correctly.

**Fix:** Check which machine you are on. Cloud should ONLY draft, read, and plan.
Execution always happens on local. If you are on local and see this, check `.env.local`:

```bash
python -c "from config import cfg; print('mode:', cfg.mode)"
# If it prints 'cloud' on your local machine:
# Edit .env.local and set DEPLOYMENT_MODE=local
```

---

### P-02 — `ImportError: cannot import name 'ModeError' from 'config'`

**Cause:** Old version of `config.py` before `ModeError` was added, or `config.py`
from a different project is on `sys.path`.

**Fix:**
```bash
python -c "
import sys; sys.path.insert(0, '.')
from config import cfg, ModeError
print('config.py version OK — ModeError available')
"
# If it fails, check that config.py is the Platinum version:
python -c "
from pathlib import Path
text = Path('config.py').read_text()
print('has ModeError:', 'ModeError' in text)
print('has assert_allowed:', 'assert_allowed' in text)
print('has dry_run_guard:', 'dry_run_guard' in text)
"
```

---

### P-03 — `DRY_RUN` not respected — actions execute anyway

**Cause:** The component doesn't call `cfg.dry_run_guard()` before executing.
Or the `.env.local` file is not loaded (dotenv not installed, wrong working directory).

**Diagnosis:**
```bash
python -c "
from config import cfg
print('DRY_RUN env raw:', __import__('os').environ.get('DRY_RUN', 'NOT SET'))
print('cfg.is_dry_run():', cfg.is_dry_run())
"
```

**Fix:** Ensure the component's confirm/execute block starts with:
```python
if cfg.dry_run_guard(f"send invoice {amount}", "component_name"):
    return  # skipped in dry-run
```

---

## rate_limiter.py

### P-04 — `RateLimitError: Rate limit exceeded for 'odoo_write'` — always fires

**Cause:** The rate limit window hasn't expired from a previous test run, or
`RATE_LIMIT_ODOO_WRITE` is set to 0 (cloud default — never write from cloud).

**Diagnosis:**
```bash
python rate_limiter.py --status
# Shows remaining quota and window reset time for each action
```

**Fix option 1** — Reset a specific action:
```bash
python rate_limiter.py --reset odoo_write
```

**Fix option 2** — Check env var override:
```bash
python -c "
import os
from pathlib import Path
text = Path('.env.local').read_text() if Path('.env.local').exists() else ''
for line in text.splitlines():
    if 'RATE_LIMIT' in line:
        print(line)
"
# RATE_LIMIT_ODOO_WRITE=0 means 'never allow' — change to 10 or remove
```

---

### P-05 — `vault/Logs/.rate_limits.json` — `PermissionError` or `json.JSONDecodeError`

**Cause:** Two processes tried to write the file simultaneously (race), or
the file was corrupted by a hard kill.

**Fix:**
```bash
python -c "
from pathlib import Path
f = Path('vault/Logs/.rate_limits.json')
if f.exists():
    try:
        import json; json.loads(f.read_text())
        print('Rate limits file: OK')
    except json.JSONDecodeError:
        f.unlink()
        print('Corrupted — deleted. Rate limiter will start fresh.')
else:
    print('File missing — will be created on first check_and_record call')
"
```

---

## vault_sync.sh / vault_sync_windows.py

### P-06 — Git merge conflict in `vault/Dashboard.md`

**Symptom:** `vault_sync.sh` or `git pull` fails with:
```
CONFLICT (content): Merge conflict in vault/Dashboard.md
Automatic merge failed; fix conflicts and then commit the result.
```

**Cause:** Both cloud and local rewrote Dashboard.md in the same sync window.
By design, local always wins — Dashboard is regenerated from live state.

**Fix:**
```bash
# Accept local version (always correct for Dashboard)
git checkout --ours vault/Dashboard.md
git add vault/Dashboard.md
git rebase --continue   # if in rebase
# or:
git commit -m "resolve Dashboard.md conflict — local wins"
```

Or add this to your `.gitattributes` to prevent future conflicts:
```
vault/Dashboard.md merge=ours
```

---

### P-07 — `vault_sync.sh` exits 1 — `error: failed to push some refs`

**Cause:** Cloud VM's local branch is behind remote (another machine pushed after
the last pull). The `git pull --rebase` should fix this but failed.

**Diagnosis:**
```bash
git status
git log --oneline -5
git log --oneline origin/main -5
```

**Fix:**
```bash
git fetch origin
git rebase origin/main
# Resolve any conflicts, then:
git push
```

If conflicts are in `vault/Logs/` (which should NOT be synced):
```bash
# Add to .gitignore and remove from tracking:
echo "vault/Logs/" >> .gitignore
git rm -r --cached vault/Logs/
git commit -m "stop tracking vault/Logs"
```

---

### P-08 — Vault sync runs but nothing changes on the other machine

**Cause 1:** `.gitignore` is too broad — the new files are being ignored.

```bash
git check-ignore -v vault/Needs_Action/email/EMAIL_test.md
# If it prints a rule, that's why it's not syncing
```

**Fix:** Edit `.gitignore` — ensure only `Logs/`, `Queue/`, `In_Progress/`,
`Approved/`, `Rejected/`, and session files are excluded, NOT `Needs_Action/`.

**Cause 2:** Cron/Task Scheduler not running.

```bash
# Linux — check cron
crontab -l | grep vault_sync
# If missing: bash scripts/setup_sync_cron.sh

# Windows — check Task Scheduler
schtasks /query /tn "VaultSync" /fo LIST
# If missing: powershell scripts/setup_sync_windows.ps1
```

**Cause 3:** SSH key for GitHub push not set up.

```bash
ssh -T git@github.com
# Should print: Hi USERNAME! You've successfully authenticated
# If not: generate an SSH key and add it to GitHub Settings → SSH keys
```

---

### P-09 — `vault_sync_windows.py` — `subprocess.CalledProcessError` on git pull

**Cause:** Windows git uses a different credential store and the token has expired.

**Fix:**
```bash
# Re-authenticate:
git -C "D:\Heck ---0\AI Empolyee" pull
# Browser may open for GitHub login

# Or use SSH instead of HTTPS:
git remote set-url origin git@github.com:USER/ai-employee.git
```

---

## Docker / Odoo

### P-10 — `docker compose up` fails — `port is already allocated`

**Cause:** Another service is already using port 80, 443, or 5432.

**Diagnosis:**
```bash
# Linux:
sudo ss -tlnp | grep -E '80|443|5432'
# Windows:
netstat -ano | findstr ":80 \|:443 \|:5432 "
```

**Fix:** Stop the conflicting service or change the port mapping in `docker-compose.yaml`:
```yaml
# Change Nginx ports if 80/443 are taken:
ports:
  - "8080:80"
  - "8443:443"
```

---

### P-11 — Odoo shows blank page / 502 Bad Gateway after docker compose up

**Cause:** Odoo container hasn't finished initializing. First start takes 60–120s
for database creation.

**Fix:**
```bash
# Watch logs until "Odoo is running" appears
docker compose logs -f odoo
# Normal startup lines:
# odoo  | 2026-03-02 ... INFO ? odoo: HTTP service (werkzeug)
# nginx | ... start worker processes

# Quick check:
curl -s -o /dev/null -w "%{http_code}" http://localhost:8069/web/login
# Should return 200 once ready
```

---

### P-12 — `odoo_health.py` reports SSL check FAIL — certificate expired or not found

**Cause:** Let's Encrypt cert not issued or expired. `init-letsencrypt.sh` not run.

**Fix:**
```bash
cd docker/
# Check cert expiry
echo | openssl s_client -connect YOUR_DOMAIN:443 2>/dev/null | openssl x509 -noout -dates

# Re-issue cert:
docker compose run --rm certbot renew
docker compose exec nginx nginx -s reload

# If initial cert was never issued:
bash init-letsencrypt.sh
```

---

### P-13 — Odoo backup fails — `pg_dump: command not found`

**Cause:** `pg_dump` is not installed on the host, or the PostgreSQL container
isn't running when `odoo_backup.sh` tries to exec into it.

**Fix:**
```bash
# Check container is up:
docker compose ps postgres

# Run pg_dump inside the container:
docker compose exec -T postgres pg_dump -U odoo odoo_db > backup.sql

# odoo_backup.sh uses docker exec internally — check the script's CONTAINER_NAME
# variable matches your compose service name:
grep "CONTAINER" scripts/odoo_backup.sh
```

---

### P-14 — Odoo `_assert_not_cloud` guard triggers on local machine

**Symptom:** `ValueError: odoo_confirm_invoice is blocked when running on cloud VM`
appears even when running locally.

**Cause:** `odoo_mcp.py` reads `DEPLOYMENT_MODE` from environment, which might
be `cloud` in your shell session even on the local machine.

**Fix:**
```bash
# Check what DEPLOYMENT_MODE your shell sees:
echo $DEPLOYMENT_MODE   # Linux
$Env:DEPLOYMENT_MODE    # Windows PowerShell

# Override for this session:
set DEPLOYMENT_MODE=local   # Windows cmd
$Env:DEPLOYMENT_MODE="local"  # PowerShell

# Long-term: ensure .env.local has DEPLOYMENT_MODE=local
# and that python-dotenv loads it before the MCP starts
```

---

## social_drafter.py / Playwright Posters

### P-15 — `social_drafter.py` on cloud blocks with `ModeError: social_post_twitter`

**Cause:** You tried to call `social_post_twitter` from the cloud drafter.
The drafter should ONLY call `social_draft`, which is allowed.

**This is correct behavior.** The drafter writes to `Twitter_Drafts/` — it never posts.
Posting happens via `twitter_poster.py` on the local machine only.

**If this fires for `social_draft`:** check `CLOUD_BLOCKED` list in `config.py` —
`social_draft` should NOT be in it.

---

### P-16 — Playwright poster: `Error: No usable cookies found — log in first`

**Cause:** The Playwright session cookie for Twitter/Meta/LinkedIn is missing
or expired. Session files live in `.twitter_session/`, `.meta_session/`, `.linkedin_session/`.

**Fix:**
```bash
# Re-run the login flow for the relevant poster:
python watchers/twitter_poster.py --login     # opens browser, log in manually
python watchers/meta_poster.py --login
python watchers/linkedin_poster.py --login
# Session saved to .<platform>_session/ after login
```

---

### P-17 — Draft stays in `Twitter_Drafts/` — poster ignores it

**Cause 1:** Draft has `status: draft` — poster only picks up `status: ready`.

**Fix:** Open the draft file and change frontmatter:
```bash
# Edit the file and change status: draft → status: ready
# Or use:
python -c "
from pathlib import Path
import sys
fname = sys.argv[1]
p = Path(fname)
text = p.read_text(encoding='utf-8').replace('status: draft', 'status: ready', 1)
p.write_text(text, encoding='utf-8')
print('Updated:', fname)
" vault/Twitter_Drafts/DRAFT_TWITTER_xyz.md
```

**Cause 2:** `DRY_RUN=true` — poster simulates but doesn't post.

```bash
python -c "from config import cfg; print('DRY_RUN:', cfg.is_dry_run())"
# Set DRY_RUN=false in .env.local to enable real posting
```

---

### P-18 — Meta poster: Instagram post fails, Facebook succeeds

**Cause:** Instagram requires an image for most post types. Text-only posts
are restricted on Instagram via web automation.

**Fix:** Add an `image_path` to the draft frontmatter or set `platform: facebook`
to skip Instagram:
```yaml
---
platform: facebook
# remove: platform: both
---
```

---

## claim_agent.py

### P-19 — `claim_agent.py` fails with `ERROR: vault/In_Progress/orchestrator does not exist`

**Cause:** `setup_vault_structure.py` hasn't been run, so the In_Progress agent
folder doesn't exist yet.

**Fix:**
```bash
python scripts/setup_vault_structure.py
# Then retry:
python scripts/claim_agent.py --agent orchestrator
```

---

### P-20 — Two claim agents both think they claimed the same task

**Cause:** Both ran the `if dest.exists()` check before either completed the rename.
This is a TOCTOU race if running across a network filesystem.

**Note:** On the same filesystem (local NVMe/SSD), `Path.rename()` is atomic.
This race cannot happen on the same machine.

**On network filesystems (NFS, SMB):** Rename atomicity is not guaranteed.
Use the Redis-based offline queue or a SQLite-based claim lock instead.

**Diagnosis:**
```bash
# Check if two copies of the file exist:
python -c "
from pathlib import Path
for d in Path('vault/In_Progress').iterdir():
    for f in d.iterdir():
        print(f'{d.name}/{f.name}')
"
```

---

## PM2 / Process Management

### P-21 — PM2 shows process as `errored` / `stopped` immediately after start

**Cause 1:** Python script exits with error on startup (missing `.env.local`,
missing vault directories, import error).

**Diagnosis:**
```bash
pm2 logs orchestrator --lines 50
# or check the log file directly:
type logs\orchestrator.log | findstr /n "Error\|Traceback" | head /n 20
```

**Cause 2:** `ecosystem.local.config.js` has wrong path to the Python script.

**Fix:**
```bash
node -e "const c = require('./ecosystem.local.config.js'); console.log(JSON.stringify(c.apps, null, 2))"
# Verify all script paths exist
```

---

### P-22 — PM2 on Oracle VM doesn't restart after VM reboot

**Cause:** `pm2 startup` was never run, so PM2 doesn't register as a systemd service.

**Fix:**
```bash
# On Oracle VM:
pm2 startup systemd -u ubuntu --hp /home/ubuntu
# Follow the output command — it will be something like:
sudo env PATH=$PATH:/usr/bin pm2 startup systemd -u ubuntu --hp /home/ubuntu
pm2 save
# Test:
sudo reboot
# After reboot:
pm2 list   # should show all processes running
```

---

## General Sync / Cloud Errors

### P-23 — `git push` rejected — `remote: Repository not found`

**Cause:** The repo URL in `.git/config` is wrong, or the GitHub token/SSH key
doesn't have push access.

**Fix:**
```bash
git remote -v   # check URL
git remote set-url origin git@github.com:YOUR_USERNAME/ai-employee.git
ssh -T git@github.com   # verify SSH key works
```

---

### P-24 — `vault/Logs/` files appearing in `git status`

**Cause:** Logs were committed before `.gitignore` was set up, or someone did
`git add vault/` without checking.

**Fix:**
```bash
# Remove from tracking without deleting files:
git rm -r --cached vault/Logs/
git rm -r --cached vault/Queue/
git rm -r --cached vault/In_Progress/
git commit -m "stop tracking runtime-only vault dirs"

# Ensure .gitignore has:
# vault/Logs/
# vault/Queue/
# vault/In_Progress/
```

---

### P-25 — Both machines modifying `vault/Needs_Action/` — task processed twice

**Cause:** Cloud creates a task, syncs it. Local processes it and moves to Done.
Cloud's next sync sees the missing file but doesn't know it's done — no conflict,
but the cloud might re-create the task from the same email (deduplication gap).

**Fix:** Ensure `gmail_watcher.py` persists processed message IDs in
`watchers/.gmail_processed_ids`. Even if the task card is gone, the ID is marked done.

```bash
# Check how many IDs are tracked:
python -c "
from pathlib import Path
f = Path('watchers/.gmail_processed_ids')
if f.exists():
    ids = [l for l in f.read_text().splitlines() if l.strip()]
    print(f'{len(ids)} Gmail message IDs tracked (no re-processing)')
else:
    print('WARNING: .gmail_processed_ids missing — emails may be re-processed')
"
```

---

## Diagnostic Quick Reference

```bash
# Check deployment mode
python -c "from config import cfg; print('mode:', cfg.mode, '| dry_run:', cfg.is_dry_run())"

# Check all rate limit quotas
python rate_limiter.py --status

# Check pending approvals
python watchers/approval_watcher.py --list

# Show Platinum audit events today
python -c "
import json, datetime
from pathlib import Path
today = datetime.date.today().isoformat()
log = Path(f'vault/Logs/AUDIT_{today}.jsonl')
if log.exists():
    lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    plat = [l for l in lines if l.get('component','') in
            {'social_drafter','approval_watcher','claim_agent','rate_limiter','config'}]
    print(f'{len(plat)} Platinum events. Last 10:')
    for e in plat[-10:]:
        print(f'  [{e.get(\"severity\",\"?\")[:4]}] {e.get(\"component\",\"?\")} {e.get(\"event\",\"?\")} {e.get(\"action\",e.get(\"error\",\"\"))[:50]}')
"

# Check git sync status
git log --oneline -5
git status --short

# Check Odoo Docker status
docker compose -f docker/docker-compose.yaml ps

# Check PM2 processes
pm2 list
```
