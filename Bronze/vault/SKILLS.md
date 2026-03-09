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
