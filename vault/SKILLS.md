# AI Employee Skills

> Capabilities the AI Employee can perform, organized by tier.

## Bronze Tier
- Detect new files in Inbox
- Create task cards in Needs_Action

## Silver Tier
- Monitor Gmail inbox for new emails and create task cards
- Monitor WhatsApp messages via Web and create task cards
- Trigger Claude AI processing on new task cards
- Classify emails: urgent / reply_needed / informational / spam
- Extract WhatsApp message metadata (sender, group, media)
- Auto-prioritize based on sender and keyword detection

## Silver Tier — Email Sending (MCP)
- Send emails via Gmail SMTP through Claude using MCP tools
- HITL flow: draft_email (preview) -> human approves -> send_email
- List and discard pending drafts before sending
- All sent emails logged to vault/Sent_Emails/ as .md files
- Supports To, CC, Subject, plain-text Body

## Silver Tier — Approval Workflow (HITL)
- Sensitive actions require human approval before execution
- Sensitive action types: send_email, delete_file, move_to_archive, reply_whatsapp, forward_email
- Flow: Plan generated -> if action is sensitive -> card moves to Pending_Approval/
- Human reviews card in Pending_Approval/ and moves it to Approved/ to greenlight
- Watcher detects file in Approved/ -> triggers MCP or Claude to execute the action
- Non-sensitive actions (classify, summarize, extract) execute automatically
- Priority override: tasks with priority "high" always require approval regardless
- All approvals/rejections are logged with timestamp for audit trail

## Silver Tier — LinkedIn Posting
- Generate LinkedIn posts via Claude CLI (topic + tone → polished draft)
- Save drafts to vault/LinkedIn_Drafts/ for human review before posting
- Schedule posts for future dates/times via frontmatter `schedule:` field
- Publish posts to LinkedIn via Playwright browser automation
- Watcher mode: continuously monitors LinkedIn_Drafts/ for ready posts
- On-demand mode: generate + post a single draft from CLI
- Deduplication via content hashing (prevents double-posting)
- All posts logged to vault/LinkedIn_Posted/ with full content + timestamp
- HITL: drafts are always saved first — human edits/approves before posting
- Rate limiting: 30s delay between consecutive posts
- Supported tones: professional, casual, thought-leadership

## Silver Tier — Plan Generation
- When a new task card appears in Needs_Action, generate a Plan.md
- Plan includes: summary, priority assessment, recommended actions, deadline estimate
- Plans are saved to vault/Plans/ with matching filename
- Plan format: YAML frontmatter + step-by-step action list
- Claude reads the task card + SKILLS.md, outputs a structured plan

## Gold Tier — Odoo ERP Integration (MCP)
- Connect to local Odoo 19 via JSON-RPC through Claude using MCP tools
- HITL flow: odoo_draft_invoice (preview) → human approves → odoo_confirm_invoice
- HITL flow: odoo_draft_payment (preview) → human approves → odoo_confirm_payment
- Read invoices, payments, partners, and products directly (no approval needed)
- Generate financial summary reports (this_month, last_month, this_year, all)
- All write actions create drafts in vault/Odoo_Drafts/ before touching Odoo
- Invoices and payments created in Odoo DRAFT state (human validates in Odoo UI)
- Full audit trail: every confirmed action logged to vault/Odoo_Logs/ as JSON
- List and discard pending Odoo drafts at any time
- Partner/product lookup with partial name matching

## Gold Tier — Facebook & Instagram Posting (Meta Graph API)
- Post to Facebook Page: text posts and photo posts (with image URL)
- Post to Instagram Business: single images and carousels (2-3 step container flow)
- HITL flow: drafts saved to vault/Meta_Drafts/ → human sets status: ready → poster publishes
- Watcher mode: continuously monitors Meta_Drafts/ for ready posts
- On-demand mode: post a single draft from CLI
- Deduplication via content hashing (prevents double-posting)
- All posts logged to vault/Meta_Posted/ with full content + timestamps
- Rate limiting: 30s delay between consecutive posts
- Cross-platform: set platform to "both" to post to FB and IG simultaneously
- Supports scheduled posting via frontmatter `schedule:` field
- CLI verification: `python meta_poster.py verify` tests API connection

## Gold Tier — Meta Social Media Summary
- Fetch recent posts from Facebook Page with engagement metrics (likes, comments, shares)
- Fetch recent media from Instagram Business with like/comment counts
- Generate formatted markdown summary saved to vault/Plans/
- AI-enhanced mode: Claude adds actionable recommendations and trend analysis
- Profile overview: follower counts, page likes, total media count
- Per-post breakdown with engagement stats and top-performer identification
- Supports configurable post limits for analysis depth

## Gold Tier — Twitter/X Integration
- Post text tweets (≤280 chars) and threads (up to 25 tweets) via API v2
- Fetch recent timeline with full engagement metrics (likes, RTs, replies, impressions)
- Engagement rate calculation: interactions / impressions × 100
- Draft-file watcher: drop .md in vault/Twitter_Drafts/ → set status: ready → auto-posts
- Thread format: separate tweets with ---tweet--- separator in draft body
- MCP tool access: twitter_post_tweet, twitter_post_thread, twitter_get_timeline
- Summary script: vault/Plans/TWITTER_SUMMARY_YYYY-MM-DD.md
- Rate limiting: 30s delay between posts; OAuth 1.0a for writes, Bearer Token for reads

## Gold Tier — Ralph Wiggum Autonomous Loop
- Runs multi-step autonomous tasks that span many Claude iterations
- Two modes: External Loop (orchestrator/CLI) and Stop Hook (interactive Claude Code session)
- External Loop: ralph_loop.py calls `claude --print` in a while-loop until done signal fires
- Stop Hook: `.claude/settings.json` hook calls ralph_loop.py --hook-check after every Claude stop
  - Exit code 2 = Claude keeps going; exit code 0 = Claude stops normally
- Completion signal types: signal_file (glob match), empty_dir, all_handled (frontmatter status), file_count
- State persisted in .ralph_state.json — survives restarts, supports --abort and --status
- Daily Needs_Action audit: runs at 10:00 PKT, classifies all cards, creates Plan.md for actionable items
- Completion logs saved to vault/Plans/RALPH_LOG_<label>_<date>.md
- Orchestrator integration: run_ralph_loop() launches external loop as background Popen subprocess
- CLI: python ralph_loop.py --task "..." --done-type signal_file --done-glob "vault/Plans/COMPLETE_*.md"
- Hook init: python ralph_loop.py --init-hook --task "..." → copy prompt → paste into Claude Code
- Safe by default: max-iter cap (default 15), per-iteration batch limit, human can --abort at any time

## Platinum Tier — Domain Specialization

### Cloud Domain (Oracle Always Free VM — DRAFT-ONLY)
**Runs 24/7 on Oracle ARM VM (4 OCPU / 24 GB RAM). No paid APIs required.**

- Email triage: gmail_watcher monitors inbox, creates task cards in Needs_Action/email/
- Email draft generation: Claude generates reply drafts (status: draft) → human approves locally
- Social draft generation: social_drafter.py picks up Needs_Action/social/ tasks → generates
  Twitter, Facebook/Instagram, and LinkedIn drafts via Claude → saves with status: draft
- Social analytics (read-only): twitter_summary.py, meta_summary.py — no writes
- Odoo read-only: ceo_briefing reads invoices and partners for weekly briefing report
- Plan generation: ralph_loop.py + orchestrator — generate Plans/*.md autonomously
- Task claiming: claim_agent.py — atomic Needs_Action → In_Progress rename
- Vault sync: vault_sync.sh (cron every 5 min) — push drafts/plans to GitHub → local machine pulls
- Config guard: config.py DEPLOYMENT_MODE=cloud blocks all sends, posts, and confirms automatically
- PM2 processes: watchdog, orchestrator, gmail_watcher, social_drafter, claim_agent, ralph-loop

**Cloud NEVER does:**
  - Send emails (no GMAIL_APP_PASSWORD set)
  - Confirm Odoo invoices or payments (no ODOO_PASSWORD set)
  - Post to Twitter/X, Facebook, Instagram, LinkedIn (Playwright posters LOCAL only)
  - Access WhatsApp (requires phone — LOCAL only)
  - Execute approved actions (approval_execute blocked)

### Local Domain (Windows Machine — FULL ACCESS)
**Runs when machine is on. Human-in-the-loop for all final actions.**

- Social posting via Playwright (zero API cost, no API keys needed):
  → Twitter/X: twitter_poster.py — human sets status: ready in vault/Twitter_Drafts/
  → Facebook + Instagram: meta_poster.py — human sets status: ready in vault/Meta_Drafts/
  → LinkedIn: linkedin_poster.py — human sets status: ready in vault/LinkedIn_Drafts/
- WhatsApp monitoring: whatsapp_watcher.py — Playwright browser, phone QR session
- Email sending: email_mcp.js — send_email tool, HITL draft-first flow
- Odoo confirms: odoo_mcp.py — odoo_confirm_invoice, odoo_confirm_payment (HITL)
- Approval execution: reads vault/Pending_Approval/<domain>/ — human moves to Approved/
- Vault sync: vault_sync_windows.py (Windows Task Scheduler every 5 min)
- PM2 processes: orchestrator-local, claim-agent-local, whatsapp_watcher, twitter_poster,
  meta_poster, linkedin_poster, ralph-loop-local

**Playwright social posting (free, no API needed):**
  - First run: browser opens → log in manually → session saved
  - Subsequent runs: session auto-restored, headless posting
  - Twitter/X: no API key ($100/mo) → Playwright browser automation
  - Meta (FB/IG): no Graph API approval → Playwright browser automation
  - LinkedIn: no partner API → Playwright browser automation

### DEPLOYMENT_MODE config split
| Env var | .env.cloud | .env.local |
|---|---|---|
| DEPLOYMENT_MODE | cloud | local |
| GMAIL_APP_PASSWORD | OMITTED (no sends) | set |
| ODOO_PASSWORD | OMITTED (no writes) | set |
| X_PASSWORD | OMITTED (no posting) | set |
| META_FB_PAGE_NAME | OMITTED | set |
| AUTO_APPROVE_BELOW | none | low |

### End-to-end social media workflow (cloud → local)
1. User or orchestrator drops task in vault/Needs_Action/social/
2. Cloud: social_drafter.py generates platform drafts (status: draft)
3. Cloud: vault_sync.sh pushes drafts to GitHub
4. Local: vault_sync_windows.py pulls from GitHub
5. Human reviews draft in vault/Twitter_Drafts/ (or Meta_Drafts/, LinkedIn_Drafts/)
6. Human edits + sets `status: ready` in the frontmatter
7. Local: poster script (twitter_poster.py etc.) detects ready draft → posts via Playwright
8. Draft marked `status: posted`, log saved to vault/Twitter_Posted/ etc.

## Gold Tier — Weekly CEO Briefing (Monday Morning Audit)
- Runs automatically every Sunday at 23:00 PKT via orchestrator
- Reads vault/Business_Goals.md for revenue targets, KPI thresholds, audit rules
- Pulls live Odoo data: invoices, payments, vendor bills, overdue A/R, stale drafts
- Subscription audit: scans vendor bills for subscription keywords, flags unapproved ones
- Compares against approved subscription list in Business_Goals.md
- KPI scorecard: 6 KPIs evaluated with PASS/WARN/FAIL/ALERT status
- Reads latest social media summaries (META_SUMMARY_*.md, TWITTER_SUMMARY_*.md)
- Reads pending vault/Needs_Action/ cards and high-priority items
- Calls Claude to generate full executive briefing narrative (8 sections)
- Output: vault/Plans/CEO_BRIEFING_YYYY-MM-DD.md + raw data JSON companion file
- Manual trigger: python watchers/ceo_briefing.py [--force] [--no-odoo] [--no-claude]
- Sections: Executive Summary, Financial Performance, KPI Scorecard, Subscription Audit,
  Social Media Report, Pending Actions, Weekly Priorities, Alerts & Red Flags
