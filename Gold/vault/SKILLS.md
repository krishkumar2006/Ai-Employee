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
