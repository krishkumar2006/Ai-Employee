# Lessons Learned — Platinum Tier

> Retrospective for the full cloud/local split build.
> Gold Tier lessons are in `docs/LESSONS_LEARNED.md`.
> This file covers Platinum-specific observations.

---

## Project Summary

| Field | Value |
|-------|-------|
| Project | Personal AI Employee |
| Tier achieved | Platinum |
| Total Python LOC | ~7,200 (Gold: ~3,500 + Platinum: ~3,700) |
| New files (Platinum) | 18 Python · 3 shell · 2 JS · 4 Docker/Nginx · 5 docs |
| Cloud infrastructure | Oracle Cloud VM (free tier, ARM64) |
| Local machine | Windows 11 |
| Sync mechanism | GitHub (git push/pull, 5-min cron) |
| Social posting | Playwright browser automation (no paid APIs) |
| ERP | Odoo 17 Community (Docker, self-hosted) |
| External paid services | None |

---

## What Worked Exceptionally Well

### 1. Vault-as-Database: The Right Abstraction for Human-AI Collaboration

Using the Git-synced vault as the communication medium between cloud and local
turned out to be the single best architectural decision of the Platinum build.
Every intermediate state is a readable file. Every handoff is a file rename.
The human can inspect the current state of the system by just opening a folder.

There is no hidden state. No database query needed. Claude can read and reason
about the vault directly because it's all plain text.

**Lesson:** When the human needs to stay in the loop, design your state machine
so every state is a visible, readable file. File-based state is observable,
debuggable, and naturally auditable — no instrumentation needed.

---

### 2. The Cloud/Local Split Enforced Discipline

Forcing a strict boundary between what the cloud can do (`DEPLOYMENT_MODE=cloud`)
and what only runs locally (`DEPLOYMENT_MODE=local`) made the system's security
properties explicit and enforceable rather than implicit and optional.

When `cfg.assert_allowed()` raises `ModeError`, the error message is clear and
immediately actionable. Developers (and Claude) can't accidentally make a cloud
function post to Twitter — it's a hard error, not a documentation guideline.

**Lesson:** Don't implement security through documentation ("don't call this on
cloud"). Implement it as a runtime assertion in code. The extra 5 minutes writing
`assert_allowed()` saves hours of debugging accidental executions.

---

### 3. Playwright for Social Posting Was Genuinely Free and Reliable

The initial concern was that Playwright browser automation would be fragile
(DOM changes, bot detection, CAPTCHA). In practice, for personal-scale usage
(1–2 posts per platform per day), none of these were a problem.

Twitter/X was the most robust. LinkedIn occasionally required re-authentication
(~once per 2 weeks). Meta (Facebook) was most sensitive to UI changes.

The key insight: **save the session cookie, don't log in on every post**.
Re-authentication only when the session expires.

**Lesson:** For personal-scale social automation, Playwright + saved sessions
beats paying $100+/month for API access. Rate limits and bot detection are
designed for bulk scrapers, not for 2 posts per day.

---

### 4. Rate Limiter as a "Confidence Layer"

The sliding-window rate limiter (`rate_limiter.py`) was originally added as a
safety mechanism. It ended up being much more valuable than expected as a
**confidence layer** — knowing that even if Claude made 50 consecutive Odoo calls,
only 10 would actually execute in a 24-hour window.

This made it much easier to trust the system in production. The rate limiter is
the last line of defense before any external action, and seeing it in `--status`
output gives an at-a-glance view of system activity.

**Lesson:** Rate limiting on AI-driven systems is not just about protecting APIs
from abuse — it's about building operator confidence. A system you can trust to
not go wild is a system you're willing to run 24/7.

---

### 5. `claim_agent.py` Atomic Rename Was Worth the Complexity

The claim-by-rename pattern (atomic `os.rename` / `Path.rename`) solved a real
concurrency problem elegantly. Multiple agents can watch the same `Needs_Action/`
folder, and exactly one will claim each task with no duplicates and no locks.

The stale-task recovery (return tasks to queue after 1 hour in In_Progress) was
also critical — it handles the "agent crashes after claiming but before completing"
scenario automatically.

**Lesson:** When you can't use a database transaction, use a filesystem rename.
On a single machine, rename is the cheapest atomic operation available.

---

## What Was Harder Than Expected

### 1. Git Sync as a Communication Protocol Has Real Edge Cases

The 5-minute git push/pull sync worked well for the happy path, but edge cases
accumulated:

- **Dashboard.md conflicts**: Both machines regenerated it within the same window.
  Solution: `git checkout --ours` strategy + `.gitattributes` merge=ours.
- **Deleted files on one side**: When local moves a task to Done and cloud's next
  pull deletes it from Needs_Action, cloud doesn't know it was completed.
  Workaround: Keep a `completed_ids.json` for idempotency checking.
- **Large vault commits**: After a week with no sync, 200+ files accumulate and
  the push takes 10+ seconds. Solution: sync more frequently.

**Lesson:** Git is a version control system, not a message queue. It works as a
sync mechanism for small file counts, but you'll hit edge cases. Document them
explicitly (see `docs/VAULT_SYNC_GUIDE.md`) and add `--dry-run` to your sync script.

---

### 2. Docker Odoo on ARM64 Required Extra Work

Oracle Cloud's free tier is ARM64 (Ampere A1). Official Odoo images are
published for both `amd64` and `arm64`, but some addons and PostgreSQL client
tools had subtle version mismatches.

Key fixes:
- Pinned `postgres:15-alpine` specifically (not `postgres:latest`)
- Added `platform: linux/arm64` in `docker-compose.yaml` for determinism
- Certbot in Alpine required `certbot-nginx` package, not the generic certbot

**Lesson:** Always pin your Docker image tags. `latest` on ARM64 will break you
at the worst possible time. Test on the target architecture before going live.

---

### 3. Nginx Configuration for Odoo Is Non-Trivial

Odoo's WebSocket-based live chat (`/websocket`), longpolling (`/longpolling`),
and the database selector (`/web/database/`) all require specific Nginx handling.
Getting this wrong resulted in:
- Broken live updates (WebSocket timeout)
- Database selector exposed to the internet (security issue)
- Odoo login loops (cookie domain mismatch with proxy_mode)

The key was `proxy_mode = True` in `odoo.conf` plus the exact Nginx headers
(`X-Forwarded-For`, `X-Real-IP`, `Host`) that Odoo expects.

**Lesson:** Read the Odoo deployment documentation before writing the Nginx config.
The Odoo-specific Nginx config is different enough from standard reverse proxy
setups that copy-pasting a generic config will break 3+ things.

---

### 4. Approval Workflow UX Is the Weakest Link

The human approval step (`approval_watcher.py --approve FILE.md`) requires the
operator to know exactly which files are pending. In practice, this means:
1. SSH into the machine (or be at the local machine)
2. Run `--list`
3. Copy-paste the filename
4. Run `--approve`

This is too much friction for a daily workflow. The system works perfectly, but
the UX slows adoption. The ideal next step is a Telegram bot that sends approval
requests and accepts replies.

**Lesson:** The human step in a Human-in-the-Loop system must be designed for
the busiest moment of the operator's day. A 4-step CLI workflow is fine for
testing but will be skipped when things get busy. Push notifications + 1-tap
approve is the right target.

---

### 5. `social_drafter.py` + Claude Costs Add Up

The Claude API calls from `social_drafter.py` (3 platforms × N requests) are
metered. For the hackathon demo this is negligible, but in production, drafting
content for all three platforms on every social request could become expensive.

Mitigations built in:
- `RATE_LIMIT_CLAUDE_CALL=50` per 24 hours
- `RATE_LIMIT_SOCIAL_DRAFT=20` per 24 hours
- `--once` flag for cron-based runs (don't poll, just process and exit)

**Lesson:** Rate-limit your AI calls just as aggressively as your external API
calls. `claude_call` should be in your rate limiter, not just `social_post`.

---

## Architecture Decisions Worth Revisiting

### Decision 1: Git Sync vs. Redis Streams

Git works but adds 5-minute latency and has conflict edge cases. Redis Streams
(Community Edition, free) would give sub-second delivery and built-in
consumer groups (preventing double-claim).

**When to migrate:** When the 5-minute sync delay causes visible UX issues, or
when vault conflict resolution becomes a maintenance burden.

---

### Decision 2: Playwright Session Cookies vs. Official APIs

Playwright + saved sessions is free but fragile over time (UI changes, platform
anti-bot updates). Official APIs are paid but stable and supported.

**When to migrate:** When posting reliability drops below 95% in a month, or
when official API pricing becomes affordable for the posting volume.

---

### Decision 3: subprocess `claude --print` vs. Anthropic Python SDK

The CLI subprocess works but is synchronous, ~200ms overhead, and tied to CLI
version. The `anthropic` Python SDK supports streaming, model selection per call,
and async execution.

**When to migrate:** When needing streaming responses, model selection (e.g.,
haiku for classification, opus for CEO briefing), or async pipelines.

---

### Decision 4: File-backed Queue vs. SQLite

`vault/Queue/*.json` is readable and debuggable but not ACID-safe with multiple
concurrent drainers. SQLite with WAL mode would handle concurrent access safely.

**When to migrate:** When queue depth regularly exceeds 50 items, or when two
processes need to drain the same queue simultaneously.

---

## What I Would Do Differently

1. **Build the approval UX first.** The CLI `--approve FILE.md` workflow works
   but operators skip it. Design the human step as a push notification (Telegram,
   email, or even a local toast notification) before building the rest of the flow.

2. **Version the vault schema from day one.** Task cards and draft files evolved
   across Gold → Platinum, and migration was manual. A `schema_version: 1` field
   in every file's frontmatter would make upgrades scriptable.

3. **Make DRY_RUN the default.** Shipping with `DRY_RUN=true` as default and
   requiring explicit `DRY_RUN=false` to enable execution would prevent accidental
   production actions during setup. "Opt-in to execution" is safer than
   "opt-out from it".

4. **Monitor vault/Needs_Action depth daily.** Task cards accumulate when the
   processing agents are down. A simple daily alert ("N tasks unprocessed > 12h")
   would catch silent failures before they become a backlog.

5. **Document the network topology diagram before writing the sync code.**
   Understanding "who can see what" (cloud VM has no access to Windows LAN, but
   both can reach GitHub) would have prevented two refactors of the vault sync
   architecture.

---

## Metrics

_Fill in with your actual run data:_

| Metric | Value |
|--------|-------|
| Emails processed (total) | |
| Social drafts generated | |
| Social posts published via Playwright | |
| Odoo operations (drafts created) | |
| Odoo operations (confirmed by human) | |
| Vault sync cycles (cloud push) | |
| Vault sync conflicts resolved | |
| Circuit breaker trips (Odoo) | |
| Rate limit blocks (all actions) | |
| Approval watcher: auto-approved | |
| Approval watcher: human approved | |
| Approval watcher: human rejected | |
| Uptime on Oracle Cloud VM | |
| Claude API calls (estimated) | |

---

## What to Build Next (Platinum+ Ideas)

- **Telegram approval bot** — sends `[DRAFT] LinkedIn post ready — approve?` with
  inline approve/reject buttons. Eliminates CLI friction entirely.
- **Odoo queue drain on circuit close** — currently manual. Wire the circuit breaker's
  `on_close` callback to auto-drain `vault/Queue/odoo_*.json`.
- **WhatsApp auto-responder** — classify incoming WhatsApp messages and send pre-approved
  replies automatically for common queries (business hours, pricing, location).
- **Multi-tenant vault** — one orchestrator, multiple `vault_<client>/` directories.
  Each client has isolated tasks, drafts, and approvals.
- **CEO Briefing as email** — auto-send `vault/Plans/CEO_BRIEFING_*.md` as a formatted
  HTML email at 06:00 Monday morning.
- **Playwright screenshot audits** — screenshot the posted content after each Playwright
  post and save to `vault/Posted_Proof/` for audit compliance.
- **Redis pub/sub A2A messaging** — replace polling with instant push notifications
  between agents (see `docs/` A2A optional upgrade notes).
