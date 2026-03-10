# Personal AI Employee — Platinum Tier

> A self-healing, autonomous AI business assistant split across two machines:
> an **Oracle Cloud VM** (always-on intelligence) and a **Windows local machine**
> (human approval + execution). Zero paid SaaS APIs. Every action is logged,
> rate-limited, and requires human sign-off before it touches money or external accounts.

---

## Tier Achieved: Platinum

| Tier | Core Capabilities | Status |
|------|-------------------|--------|
| Bronze | File drop → task card | Done |
| Silver | Gmail → task card → Claude plan | Done |
| Gold | Odoo MCP · CEO Briefing · Circuit Breaker · Watchdog · Ralph Loop | Done |
| **Platinum** | **Cloud/Local split · Docker Odoo · Vault Sync · Social Playwright · Rate Limiter · Deployment Config** | **Done** |

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                     ORACLE CLOUD VM  (free tier, always-on)                 ║
║                                                                              ║
║  ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────────────┐  ║
║  │  gmail_watcher   │  │  social_drafter   │  │       ralph_loop         │  ║
║  │  polls Gmail     │  │  Claude → drafts  │  │  autonomous multi-step   ║
║  │  OAuth (free)    │  │  Twitter/Meta/LI  │  │  inbox audit loop        │  ║
║  └────────┬─────────┘  └────────┬──────────┘  └──────────────────────────┘  ║
║           │                     │                         │                  ║
║           ▼                     ▼                         ▼                  ║
║  ┌──────────────────────────────────────────────────────────────────────┐    ║
║  │                    vault/  (Git repository — sync bridge)            │    ║
║  │                                                                      │    ║
║  │  Needs_Action/email/   ←─ gmail_watcher writes task cards           │    ║
║  │  Needs_Action/social/  ←─ social content requests                   │    ║
║  │  Twitter_Drafts/       ←─ Claude-generated tweet/thread drafts      │    ║
║  │  Meta_Drafts/          ←─ Claude-generated Facebook/Instagram       │    ║
║  │  LinkedIn_Drafts/      ←─ Claude-generated LinkedIn posts           │    ║
║  │  Pending_Approval/     ←─ anything needing human sign-off           │    ║
║  │  Plans/                ←─ briefings, plans, signal files            │    ║
║  └──────────────────────────────┬───────────────────────────────────────┘    ║
║                                 │  vault_sync.sh  (cron every 5 min)         ║
║                                 │  git pull-rebase → commit → git push        ║
╚═════════════════════════════════╪════════════════════════════════════════════╝
                                  │
                      ┌───────────▼──────────┐
                      │     GitHub repo       │
                      │  (free private/pub)   │
                      └───────────┬──────────┘
                                  │
                      vault_sync_windows.py   (Task Scheduler every 5 min)
                      git pull --rebase
                                  │
╔═════════════════════════════════╪════════════════════════════════════════════╗
║                    WINDOWS LOCAL MACHINE  (human approval + execution)       ║
║                                 │                                            ║
║           ┌─────────────────────▼────────────────────────────┐              ║
║           │                vault/  (local copy)               │              ║
║           │  Pending_Approval/ ←── pulled from cloud          │              ║
║           │  Approved/         ──► human decisions (gitignored)│             ║
║           │  Rejected/         ──► human decisions (gitignored)│             ║
║           └─────────────────────┬────────────────────────────┘              ║
║                                 │                                            ║
║   ┌─────────────────┐  ┌────────┴───────┐  ┌────────────────────────────┐  ║
║   │approval_watcher │  │ twitter_poster │  │    whatsapp_watcher        │  ║
║   │ human approve / │  │  meta_poster   │  │   WhatsApp Web via         │  ║
║   │ reject drafts   │  │linkedin_poster │  │   Playwright (free)        │  ║
║   └─────────────────┘  │ (Playwright — │  └────────────────────────────┘  ║
║                         │  zero API cost)│                                   ║
║                         └───────────────┘                                   ║
║                                                                              ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │   Self-Hosted Odoo 17  (Docker Compose)                               │  ║
║  │                                                                       │  ║
║  │  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │  ║
║  │  │ Odoo 17  │  │ PostgreSQL 15│  │  Nginx Alpine│  │   Certbot   │  │  ║
║  │  │  :8069   │  │    :5432     │  │  SSL  :443   │  │ Let'sEncrypt│  │  ║
║  │  │ 127.0.0.1│  │  internal    │  │ proxy → Odoo │  │  (free)     │  │  ║
║  │  └──────────┘  └──────────────┘  └──────────────┘  └─────────────┘  │  ║
║  │                                                                       │  ║
║  │  odoo_mcp.py (Claude MCP)  →  HITL invoice / payment / CRM workflow  │  ║
║  └───────────────────────────────────────────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════════════════════════════╝

                    ┌──────────────────────────────────┐
                    │    Cross-cutting (both machines)  │
                    │                                  │
                    │  audit_logger.py → JSONL + HEALTH│
                    │  rate_limiter.py → sliding window│
                    │  retry_handler.py→ circuit breaker│
                    │  offline_queue.py→ durable queue  │
                    │  config.py       → mode + dry_run│
                    └──────────────────────────────────┘
```

### Full Data Flow: Email → Cloud Draft → Local Approve → Execute

```
  [Gmail Inbox]
       │  Gmail OAuth (free — no paid API)
       ▼
  [gmail_watcher.py — Cloud VM]
       │  writes → vault/Needs_Action/email/EMAIL_<subject>_<ts>.md
       │
       ▼
  [vault_sync.sh — Cloud VM — every 5 min]
       │  git add → git commit → git push
       │
       ▼
  [GitHub — free repo]
       │
       ▼
  [vault_sync_windows.py — Windows — every 5 min]
       │  git pull --rebase
       │
       ▼
  [ralph_loop.py or approval_watcher.py — Windows]
       │  human: python watchers/approval_watcher.py --approve EMAIL_xyz.md
       │
       ▼
  [Execute — Windows LOCAL ONLY]
       │  Email reply  → gmail send (OAuth)
       │  Social post  → Playwright: twitter_poster / meta_poster / linkedin_poster
       │  Odoo action  → odoo_mcp confirm (HITL guard + rate limiter)
       │  WhatsApp     → whatsapp_watcher (Playwright)
       │
       ▼
  [vault/Done/<domain>/  +  vault/Logs/AUDIT_*.jsonl]
```

---

## Design Principles

| Principle | Implementation |
|-----------|---------------|
| No paid social APIs | Playwright browser automation (Twitter/X, Meta, LinkedIn) |
| No paid SaaS | Self-hosted Odoo Docker, Community Redis (optional A2A) |
| Human approves all writes | HITL: draft → Pending_Approval → human → execute |
| Cloud never executes | `cfg.assert_allowed()` blocks write/post/confirm on cloud |
| Full audit trail | Every event → `vault/Logs/AUDIT_YYYY-MM-DD.jsonl` |
| Graceful degradation | Circuit breaker → offline queue → auto-drain on recovery |
| Rate limiting | Sliding window per action type (env-var configurable) |
| DRY_RUN mode | `DRY_RUN=true` → simulate all actions without side effects |

---

## File Map

```
AI Employee/
├── audit_logger.py           Gold  Structured JSONL logger + HEALTH.json
├── retry_handler.py          Gold  @retry decorator + 3-state CircuitBreaker
├── offline_queue.py          Gold  File-based durable queue
├── watchdog.py               Gold  Process supervisor, exponential backoff
├── orchestrator.py           Gold  Scheduler + watcher subprocess manager
├── ralph_loop.py             Gold  Autonomous multi-step Claude loop
├── config.py                 Plat  DEPLOYMENT_MODE · assert_allowed · dry_run
├── rate_limiter.py           Plat  File-backed sliding-window rate limiter
│
├── mcp/
│   ├── odoo_mcp.py           Gold  11 Odoo tools — invoice/payment/CRM (HITL)
│   ├── meta_mcp.py           Gold  Meta draft/read tools
│   └── twitter_mcp.py        Gold  Twitter draft/read tools
│
├── watchers/
│   ├── filesystem_watcher.py Brnz  File drop → task card
│   ├── gmail_watcher.py      Slvr  Gmail OAuth → task card + plan
│   ├── social_drafter.py     Plat  Cloud: Claude → platform drafts
│   ├── approval_watcher.py   Plat  Local: claim/approve/reject
│   ├── twitter_poster.py     Plat  Local: Playwright Twitter
│   ├── meta_poster.py        Plat  Local: Playwright Facebook/Instagram
│   ├── linkedin_poster.py    Plat  Local: Playwright LinkedIn
│   ├── whatsapp_watcher.py   Plat  Local: Playwright WhatsApp Web
│   ├── ceo_briefing.py       Gold  Weekly CEO Briefing (Odoo + Claude)
│   ├── update_publisher.py   Plat  Vault → cloud broadcast
│   └── update_merger.py      Plat  Cloud updates → local vault merge
│
├── scripts/
│   ├── claim_agent.py        Plat  Atomic vault task claim by rename
│   ├── dashboard_writer.py   Plat  Thread-safe Dashboard.md writer
│   ├── vault_sync.sh         Plat  Linux: git pull-rebase → push (cron)
│   ├── vault_sync_windows.py Plat  Windows: Task Scheduler vault sync
│   ├── setup_vault_structure.py Plat  Create domain subfolder layout
│   ├── odoo_backup.sh        Plat  pg_dump + filestore tar, 7-day retention
│   └── odoo_health.py        Plat  6-check Odoo health monitor + alerts
│
├── docker/
│   ├── docker-compose.yaml   Plat  Odoo 17 + PostgreSQL 15 + Nginx + Certbot
│   ├── odoo.conf             Plat  workers=3, proxy_mode=True, data_dir
│   ├── nginx/nginx.conf      Plat  SSL, WebSocket, longpolling, /db blocked
│   └── .env.docker.example   Plat  Template — no real passwords
│
├── ecosystem.cloud.config.js Plat  PM2: Oracle VM processes
├── ecosystem.local.config.js Plat  PM2: Windows local processes
│
├── vault/
│   ├── Inbox/                Drop zone
│   ├── Needs_Action/         email/ odoo/ social/ calendar/ general/
│   ├── In_Progress/          claimed tasks (not Git-synced)
│   ├── Pending_Approval/     awaiting human review
│   ├── Approved/             human-approved  (gitignored — local only)
│   ├── Rejected/             human-rejected  (gitignored — local only)
│   ├── Done/                 completed
│   ├── Plans/                briefings, signal files
│   ├── Logs/                 AUDIT_*.jsonl, HEALTH.json  (not synced)
│   ├── Queue/                offline queue JSON  (not synced)
│   ├── Twitter_Drafts/       tweet/thread drafts
│   ├── Meta_Drafts/          Facebook/Instagram drafts
│   ├── LinkedIn_Drafts/      LinkedIn post drafts
│   ├── Odoo_Drafts/          Odoo operation drafts (HITL)
│   ├── Dashboard.md          Live status  (local wins on merge conflict)
│   ├── SKILLS.md             AI employee capability list
│   └── Business_Goals.md     KPIs, revenue targets, subscription rules
│
├── docs/
│   ├── TEST_GUIDE_PLATINUM.md        Full E2E: email→cloud→approve→execute
│   ├── ERRORS_AND_FIXES_PLATINUM.md  Sync/cloud/Docker error reference
│   ├── LESSONS_LEARNED_PLATINUM.md   Platinum retrospective
│   ├── SUBMISSION_CHECKLIST_PLATINUM.md  Hackathon + security disclosure
│   └── VAULT_SYNC_GUIDE.md           Vault sync setup + troubleshooting
│
├── .env.cloud                Plat  Cloud vars (tracked, no secrets)
├── .env.local                IGNORE  Local vars (gitignored, has passwords)
└── requirements.txt          All Python dependencies (no paid packages)
```

---

## Quickstart

### Prerequisites

```bash
python --version      # 3.11+
claude --version      # Claude Code CLI
node --version        # 18+ (for PM2)
docker --version      # 24+ (for Odoo)
```

### Local Machine Setup

```bash
git clone https://github.com/YOUR_USERNAME/ai-employee.git
cd "ai-employee"

pip install -r requirements.txt
playwright install chromium          # social poster browsers

# Set up vault folder structure
python scripts/setup_vault_structure.py

# Configure local environment
cp .env.cloud .env.local             # then fill in passwords
# Required: ODOO_PASSWORD, ANTHROPIC_API_KEY (if not using claude CLI)

# Start via PM2
pm2 start ecosystem.local.config.js

# Or run directly
python watchdog.py                   # watchdog → orchestrator → watchers
```

### Cloud VM Setup

```bash
# On Oracle Cloud VM (Ubuntu, free tier):
git clone https://github.com/YOUR_USERNAME/ai-employee.git
cd ai-employee

# Vault sync cron (every 5 min)
bash scripts/setup_sync_cron.sh

# Cloud processes via PM2
pm2 start ecosystem.cloud.config.js
pm2 save && pm2 startup
```

### Odoo Docker Setup

```bash
cd docker/
cp .env.docker.example .env.docker
# Fill in: POSTGRES_PASSWORD, ODOO_MASTER_PASSWORD, DOMAIN, CERTBOT_EMAIL

# First time: provision SSL certificate
bash init-letsencrypt.sh

# Start the stack
docker compose up -d

# Odoo available at https://YOUR_DOMAIN
# Admin panel: https://YOUR_DOMAIN/odoo (port 8069 bound to 127.0.0.1 only)
```

---

## Environment Variables

### `.env.cloud` — tracked in Git, **no secrets**

```bash
DEPLOYMENT_MODE=cloud
DRY_RUN=false
ANTHROPIC_API_KEY=sk-ant-CHANGEME
GMAIL_POLL_INTERVAL=60
SOCIAL_DRAFT_POLL=120
RATE_LIMIT_CLAUDE_CALL=50
RATE_LIMIT_SOCIAL_DRAFT=20
RATE_LIMIT_ODOO_WRITE=0
```

### `.env.local` — gitignored, **has secrets**

```bash
DEPLOYMENT_MODE=local
DRY_RUN=false
ANTHROPIC_API_KEY=sk-ant-REAL_KEY
ODOO_URL=https://your-domain.com
ODOO_DB=ai-employee
ODOO_USER=admin@example.com
ODOO_PASSWORD=your_password_here
RATE_LIMIT_ODOO_WRITE=10
RATE_LIMIT_EMAIL_SEND=20
RATE_LIMIT_SOCIAL_POST=5
AUTO_APPROVE_BELOW=none
```

---

## Security Model

| Layer | Mechanism |
|-------|-----------|
| Secret isolation | Passwords only in `.env.local` (gitignored, never committed) |
| Cloud write blocking | `cfg.assert_allowed(action)` raises `ModeError` for 14 blocked actions |
| Human-in-the-loop | Odoo confirms, social posts, email sends all require approval file |
| Rate limiting | Per-action sliding window; `RateLimitError` halts before execution |
| DRY_RUN | `DRY_RUN=true` → every confirm/post/send prints `[DRY RUN]` and skips |
| Audit trail | Immutable JSONL append-only log per day per machine |
| Vault gitignore | `Approved/`, `Rejected/`, `Logs/`, `Queue/`, sessions all excluded |

---

## Free Services Used

| Service | Purpose | Cost |
|---------|---------|------|
| Claude Code CLI | AI reasoning, plans, MCP tools | Anthropic subscription |
| Gmail OAuth | Read/send email | Free (Google account) |
| Odoo Community 17 | Self-hosted ERP | Free (Docker) |
| Oracle Cloud VM | Always-on cloud process | Free tier |
| GitHub | Vault sync bridge | Free |
| Playwright | Social posting (no API keys) | Free (open source) |
| Let's Encrypt | SSL for Odoo | Free |
| Redis Community | Optional A2A message bus | Free (open source) |

**No paid social media APIs. No Zapier. No Make. No paid queues.**

---

## Documentation

| File | Contents |
|------|---------|
| `docs/TEST_GUIDE_PLATINUM.md` | Full E2E test: email → cloud draft → local approve → execute |
| `docs/ERRORS_AND_FIXES_PLATINUM.md` | Sync/cloud/Docker/rate-limiter error reference |
| `docs/LESSONS_LEARNED_PLATINUM.md` | Platinum build retrospective |
| `docs/SUBMISSION_CHECKLIST_PLATINUM.md` | Hackathon checklist + security disclosure |
| `docs/VAULT_SYNC_GUIDE.md` | Vault sync setup, conflict resolution, Syncthing alternative |
| `docs/TEST_GUIDE.md` | Gold Tier E2E tests (prerequisite) |
| `docs/ERRORS_AND_FIXES.md` | Gold Tier error reference |

---

## License

MIT — see `LICENSE` file.
