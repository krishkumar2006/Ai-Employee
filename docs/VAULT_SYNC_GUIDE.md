# Vault Sync Guide — Platinum Tier

## Overview

Two sync strategies:
- **Option A: Git** (recommended) — GitHub private repo, full history, conflict-safe
- **Option B: Syncthing** — P2P sync, no Git knowledge needed, real-time

---

## Option A: Git Sync

### Files created
| File | Purpose |
|---|---|
| `scripts/setup_vault_structure.py` | Creates domain folders + .gitkeep |
| `scripts/claim_agent.py` | Watches Needs_Action/, atomic claim-by-move |
| `scripts/dashboard_writer.py` | Thread-safe single-writer for Dashboard.md |
| `scripts/vault_sync.sh` | Git pull→commit→push (Linux/VM) |
| `scripts/vault_sync_windows.py` | Python equivalent for Windows |
| `scripts/setup_sync_cron.sh` | Installs cron on Oracle VM |
| `scripts/setup_sync_windows.ps1` | Installs Windows Task Scheduler |
| `.gitignore` | Secrets/runtime folders excluded |

### Vault folder layout (post-setup)
```
vault/
├── Needs_Action/
│   ├── email/          ← drop task .json/.md files here
│   ├── odoo/
│   ├── social/
│   ├── calendar/
│   └── general/
├── In_Progress/        ← NOT synced (runtime/transient)
│   ├── orchestrator/   ← claimed by orchestrator
│   ├── ralph/
│   ├── watcher_email/
│   ├── watcher_social/
│   └── watcher_calendar/
├── Plans/
│   ├── email/ odoo/ social/ calendar/ general/
├── Pending_Approval/
│   ├── email/ odoo/ social/ calendar/ general/
├── Done/
│   ├── email/ odoo/ social/ calendar/ general/
├── Updates/            ← broadcast update messages
├── Dashboard.md        ← single-writer (dashboard_writer.py)
├── Logs/               ← NOT synced (machine-local)
└── Queue/              ← NOT synced (transient)
```

### Initial setup (run once)

**Step 1: Create private GitHub repo**
```bash
# On GitHub.com → New repository → Private → Name: ai-employee
# Generate Personal Access Token (PAT):
# Settings → Developer Settings → Fine-grained tokens → Contents: Read+Write
```

**Step 2: Initialize Git (local machine)**
```bash
cd "D:\Heck ---0\AI Empolyee"
git init
python scripts/setup_vault_structure.py   # creates domain folders
git add .
git commit -m "Initial commit: Platinum Tier vault structure"
git remote add origin https://github.com/YOUR_USERNAME/ai-employee.git
git push -u origin main
```

**Step 3: Clone on Oracle VM**
```bash
git clone https://YOUR_PAT@github.com/YOUR_USERNAME/ai-employee.git ~/ai-employee
cd ~/ai-employee
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/setup_vault_structure.py
```

**Step 4: Install cron on VM**
```bash
bash scripts/setup_sync_cron.sh
```

**Step 5: Install Task Scheduler on Windows (PowerShell as Admin)**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_sync_windows.ps1
```

### Claim-by-move workflow
```
1. Task file appears in vault/Needs_Action/email/EMAIL_foo.json
2. claim_agent.py (running on VM) scans → renames to In_Progress/orchestrator/
3. orchestrator.py processes the task
4. On completion → move to Done/email/ or Pending_Approval/email/
5. vault_sync.sh pushes Done/ and Pending_Approval/ back to GitHub
6. Local machine pulls → Dashboard.md updated
```

### Secrets rules
Files that are NEVER committed (enforced by .gitignore):
- `.env` and `.env.*`
- `*.key`, `*.pem`, `*.p12`
- `credentials.json`, `token.json`, `*token*.json`
- `vault/Logs/` (machine-local, large)
- `vault/Queue/` (transient)
- `vault/In_Progress/` (runtime state)

### Dashboard.md single-writer
```python
from scripts.dashboard_writer import DashboardWriter

writer = DashboardWriter()
writer.start()  # starts background flush thread

# Any component can call this (thread-safe):
writer.update_section("System Status", [
    "- Orchestrator: **Running**",
    "- ralph_loop: **Running**",
])
writer.update_section("Task Summary", [
    "- Pending: **3**",
    "- Completed: **17**",
])

# On shutdown:
writer.stop()
```

Git merge strategy for Dashboard.md conflicts: **LOCAL WINS**.
Since Dashboard.md is regenerated fresh every flush cycle, whichever
machine ran most recently has the correct state.

---

## Option B: Syncthing (No-Git Alternative)

Use when: you don't want to learn Git, or you need real-time (<30s) sync.

### What is Syncthing?
- Free, open-source, P2P file sync
- No cloud middleman — syncs directly between devices
- Works through NAT/firewall using relay servers
- Web UI at `http://localhost:8384`

### Installation

**Local Windows machine:**
1. Download from https://syncthing.net/downloads/ → Windows AMD64
2. Extract → run `syncthing.exe`
3. Opens browser at `http://127.0.0.1:8384`

**Oracle VM (Ubuntu):**
```bash
# Add Syncthing repo
curl -s https://syncthing.net/release-key.txt | sudo apt-key add -
echo "deb https://apt.syncthing.net/ syncthing stable" | \
    sudo tee /etc/apt/sources.list.d/syncthing.list
sudo apt update && sudo apt install -y syncthing

# Run as systemd service
sudo systemctl enable syncthing@ubuntu
sudo systemctl start syncthing@ubuntu

# Syncthing web UI is at http://localhost:8384 (SSH tunnel to access):
# From local: ssh -L 8385:localhost:8384 oracle-ai
# Then open: http://localhost:8385
```

### Setup Syncthing sync

**Step 1: Pair devices**
1. On VM: open UI → Actions → Show ID → copy Device ID
2. On Windows: Add Device → paste VM's Device ID
3. On VM: accept the connection request from Windows

**Step 2: Share the vault folder**
1. On Windows Syncthing UI → Add Folder
   - Folder Label: `AI Employee Vault`
   - Folder Path: `D:\Heck ---0\AI Empolyee\vault`
   - Share with: Oracle VM device
2. On VM: accept the shared folder → set path to `/home/ubuntu/ai-employee/vault`

**Step 3: Set ignore patterns (equivalent to .gitignore)**

In Syncthing → Edit Folder → Ignore Patterns, add:
```
# Secrets
.env
*.key
*.pem
credentials.json
token.json

# Runtime (don't sync back and forth)
Logs
Queue
In_Progress
```

**Step 4: Conflict resolution for Dashboard.md**

Syncthing renames conflicts as `Dashboard.sync-conflict-*.md`.
To auto-resolve (local wins), add a cron on the VM:
```bash
# Delete Syncthing conflict files for Dashboard.md every minute
* * * * * find /home/ubuntu/ai-employee/vault -name "Dashboard.sync-conflict-*.md" -delete
```

### Syncthing vs Git comparison

| Feature | Git | Syncthing |
|---|---|---|
| History / rollback | Yes (full history) | No |
| Works offline | Yes (commit locally) | Partial (syncs on reconnect) |
| Conflict handling | Manual merge | Auto-rename conflicts |
| Setup complexity | Medium | Low |
| Bandwidth | Efficient (deltas) | Syncs full changed files |
| Free | Yes (GitHub free tier) | Yes (always free) |
| Best for | Multi-developer, audit trail | Personal single-user |

**Recommendation:** Use Git if you want history and audit trails (matches the
AuditLogger pattern already in the project). Use Syncthing if you want simplest
possible setup without learning Git.

---

## Cron summary (VM)

```cron
# Vault Git sync every 5 minutes
*/5 * * * * /bin/bash ~/ai-employee/scripts/vault_sync.sh >> ~/ai-employee/vault/Logs/sync.log 2>&1

# claim_agent starts on reboot
@reboot ~/ai-employee/.venv/bin/python ~/ai-employee/scripts/claim_agent.py --agent orchestrator >> ~/ai-employee/vault/Logs/claim_agent.log 2>&1

# PM2 process resurrection safety net
* * * * * pm2 resurrect > /dev/null 2>&1 || true

# Anti-idle ping
*/10 * * * * ping -c 1 8.8.8.8 > /dev/null 2>&1
```
