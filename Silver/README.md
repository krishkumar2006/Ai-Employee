# Personal AI Employee — Bronze + Silver Tier

A local-first Personal AI Employee system that monitors files, Gmail, and WhatsApp — autonomously creating actionable task cards and triggering Claude for AI processing.

## How It Works

```
You drop a file into vault/Inbox/
        ↓
Filesystem Watcher detects it instantly
        ↓
A task card (.md) is created in vault/Needs_Action/
        ↓
(Silver Tier) AI Employee picks it up and processes it
```

## Project Structure

```
AI Empolyee/
├── watchers/
│   ├── filesystem_watcher.py   # Monitors Inbox for new files (Bronze)
│   ├── gmail_watcher.py        # Monitors Gmail inbox (Silver)
│   ├── whatsapp_watcher.py     # Monitors WhatsApp Web (Silver)
│   └── linkedin_poster.py     # LinkedIn post generation + publishing (Silver)
├── mcp/
│   ├── email_mcp.js            # MCP server — Gmail email sending (Silver)
│   └── package.json            # Node.js dependencies for MCP
├── .claude/
│   └── mcp.json                # MCP server config for Claude Code
├── orchestrator.py              # Central scheduler + watcher manager (Silver)
├── ecosystem.config.js          # PM2 config for persistent operation
├── .env                        # Secrets — never commit (gitignored)
├── .env.example                # Template for .env (safe to commit)
├── .gitignore
├── logs/                        # Orchestrator + watcher logs
├── vault/
│   ├── Inbox/                  # Drop files here
│   ├── Needs_Action/           # Auto-generated task cards appear here
│   ├── Pending_Approval/       # HITL — tasks awaiting human approval
│   ├── Approved/               # HITL — human-approved tasks ready to execute
│   ├── Sent_Emails/            # Logs of emails sent via MCP
│   ├── Plans/                  # AI-generated action plans
│   ├── Done/                   # Completed tasks archive
│   ├── LinkedIn_Drafts/        # Drafts awaiting review/posting
│   ├── LinkedIn_Posted/        # Log of published LinkedIn posts
│   ├── Dashboard.md            # System status overview
│   ├── Company_Handbook.md     # Company policies (placeholder)
│   └── SKILLS.md               # AI Employee capabilities
├── TESTING_GUIDE.md             # Full E2E testing guide + 30 common errors
└── README.md
```

## Prerequisites

- Python 3.10+
- [watchdog](https://pypi.org/project/watchdog/) — filesystem monitoring
- [google-api-python-client](https://pypi.org/project/google-api-python-client/) + [google-auth-oauthlib](https://pypi.org/project/google-auth-oauthlib/) — Gmail API
- [playwright](https://pypi.org/project/playwright/) — WhatsApp Web automation
- [Claude CLI](https://docs.anthropic.com) — AI processing (optional, degrades gracefully)
- [schedule](https://pypi.org/project/schedule/) — task scheduling for orchestrator
- [PM2](https://pm2.keymetrics.io/) — process manager for persistence (optional)

## Installation

```bash
# All Python dependencies
pip install watchdog google-api-python-client google-auth-oauthlib playwright schedule

# Install Playwright browser
playwright install chromium

# (Optional) Install PM2 globally for persistence
npm install -g pm2
```

## Usage

### Option A: Orchestrator (recommended)

The orchestrator manages all watchers + scheduled tasks from a single process:

```bash
python orchestrator.py
```

This starts:
- Filesystem watcher (auto)
- Gmail watcher (auto)
- WhatsApp watcher (disabled by default — enable in `orchestrator.py` after first QR scan)
- LinkedIn poster (disabled by default — enable after first manual login)
- Morning briefing at 08:00 PKT daily
- LinkedIn draft generation at 09:00 PKT daily
- Dashboard refresh every 30 minutes
- Vault cleanup at 23:00 PKT daily
- Watcher health check every 5 minutes (auto-restart on crash)

### Option B: Individual watchers

Each watcher can still run independently:

```bash
# Bronze — filesystem
python watchers/filesystem_watcher.py

# Silver — Gmail
python watchers/gmail_watcher.py

# Silver — WhatsApp
python watchers/whatsapp_watcher.py
```

All watchers output task cards to `vault/Needs_Action/`. Stop any with `Ctrl+C`.

### Persistent operation with PM2

```bash
# Start the orchestrator as a managed daemon
pm2 start ecosystem.config.js

# Check status
pm2 status

# View live logs
pm2 logs ai-employee

# Restart after code changes
pm2 restart ai-employee

# Stop
pm2 stop ai-employee

# Auto-start on system boot (run once)
pm2 startup
pm2 save
```

## Silver Tier Features

### Multi-Channel Input
- **Filesystem Watcher** — monitors `vault/Inbox/`, creates task cards instantly
- **Gmail Watcher** — polls Gmail API for unread emails every 60s
- **WhatsApp Watcher** — scrapes WhatsApp Web via Playwright for unread messages
- **LinkedIn Poster** — generates + publishes LinkedIn posts via Claude + Playwright

### Intelligence
- **Priority Detection** — scans for urgent/asap/critical keywords → auto-sets `priority: high`
- **Deduplication** — tracks processed IDs/hashes per channel, prevents duplicate cards
- **Plan Generation** — Claude reads each task card + SKILLS.md, outputs structured plans

### Orchestration
- **Central Orchestrator** — single process manages all watchers + scheduled jobs
- **Morning Briefing** — daily at 08:00, Claude summarizes pending work
- **LinkedIn Drafts** — daily at 09:00, Claude generates a post draft for review
- **Dashboard Refresh** — every 30 minutes, updates `vault/Dashboard.md`
- **Vault Cleanup** — nightly at 23:00, archives completed cards older than 7 days
- **Health Checks** — every 5 minutes, auto-restarts crashed watchers

### Human-in-the-Loop (HITL)
- **Approval Workflow** — sensitive actions (send_email, delete_file, etc.) require human approval
- **MCP Email** — `draft_email` → human reviews preview → `send_email` only after explicit "yes"
- **LinkedIn HITL** — Claude generates draft → human edits → poster publishes
- **Audit Trail** — all approvals, rejections, and sent emails logged as `.md` files

### Persistence
- **PM2 Integration** — daemon mode with auto-restart, boot survival, log rotation
- **Session Persistence** — WhatsApp and LinkedIn browser sessions survive restarts

## Quick Test

```bash
# Terminal 1 — start everything
python orchestrator.py

# Terminal 2 — drop a test file
echo "test content" > vault/Inbox/sample_report.pdf

# Terminal 2 — verify (wait 2 seconds)
dir vault\Needs_Action\FILE_sample_report*
dir vault\Plans\FILE_sample_report*
```

For the full end-to-end testing guide covering all channels, HITL flows,
PM2 persistence, and 30 common errors with fixes, see **[TESTING_GUIDE.md](TESTING_GUIDE.md)**.

## End-to-End Flow

```
Email / File / WhatsApp / LinkedIn
            │
            ▼
    Watcher detects event
            │
            ▼
    Task card created in vault/Needs_Action/
    (with priority detection + dedup)
            │
            ▼
    Claude generates Plan.md in vault/Plans/
            │
            ├── Non-sensitive action ──► Auto-execute
            │
            └── Sensitive action ──► vault/Pending_Approval/
                                          │
                                    Human reviews
                                          │
                                    Moves to vault/Approved/
                                          │
                                          ▼
                                    Claude executes via MCP
                                    (e.g., send_email)
                                          │
                                          ▼
                                    Logged to vault/Sent_Emails/
                                    Card moved to vault/Done/
```

## Tier Roadmap

| Tier | Feature | Status |
|------|---------|--------|
| Bronze | Filesystem watcher + task card generation | Done |
| Silver | Gmail watcher + email task cards + Claude trigger | Done |
| Silver | WhatsApp watcher + message task cards + Claude trigger | Done |
| Silver | Priority detection (urgent keyword scanning) | Done |
| Silver | Deduplication (processed ID / hash tracking) | Done |
| Silver | Email MCP server (HITL draft/send via Claude) | Done |
| Silver | Sent email logging to vault/Sent_Emails/ | Done |
| Silver | Orchestrator + daily briefing + PM2 persistence | Done |
| Silver | LinkedIn poster (Claude-generated drafts + Playwright publishing) | Done |
| Gold | Full autonomous AI Employee with multi-channel orchestration | Planned |

## Troubleshooting

Most common issues and quick fixes:

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` | `pip install watchdog schedule playwright google-api-python-client google-auth-oauthlib` |
| Gmail `credentials.json not found` | Download OAuth credentials from Google Cloud Console into `watchers/` |
| Gmail token expired | Delete `watchers/gmail_token.json` and restart watcher for re-auth |
| MCP `SMTP auth failed` | Generate a new App Password at Google Account > Security > App Passwords |
| Claude CLI not found | Install Claude CLI. Task cards still work — plans/briefings won't generate. |
| WhatsApp/LinkedIn session lost | Delete the `.whatsapp_session/` or `.linkedin_session/` folder and re-login |
| PM2 `command not found` | `npm install -g pm2` |
| Watcher keeps crashing | Check `logs/<watcher_name>.log` for the real error |

For the full list of 30 errors with detailed fixes, see **[TESTING_GUIDE.md](TESTING_GUIDE.md)**.

## License

MIT
