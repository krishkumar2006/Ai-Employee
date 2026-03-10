# Silver Tier — End-to-End Testing Guide

> Step-by-step validation of every Silver Tier flow.
> Run each test in order. Every step includes the exact command, what to
> observe, and what a pass/fail looks like.

---

## Prerequisites Checklist

Before testing, confirm every dependency is in place:

```bash
# Python packages
pip install watchdog google-api-python-client google-auth-oauthlib playwright schedule

# Playwright browser
playwright install chromium

# PM2 (for persistence tests)
npm install -g pm2

# Claude CLI — verify it exists
claude --version
```

**Environment setup:**

```bash
# Copy env template if you haven't already
copy .env.example .env
# Edit .env with real Gmail credentials
```

**Folder scaffold** — the orchestrator creates these, but ensure manually:

```
vault/Inbox/
vault/Needs_Action/
vault/Pending_Approval/
vault/Approved/
vault/Done/
vault/Plans/
vault/Sent_Emails/
vault/LinkedIn_Drafts/
vault/LinkedIn_Posted/
logs/
```

---

## Test 1 — Filesystem Watcher (Bronze baseline)

This is the foundation. If this fails, nothing downstream works.

### 1A. Start the watcher

```bash
python watchers/filesystem_watcher.py
```

**Expected output:**
```
=======================================================
  AI Employee — Filesystem Watcher (Bronze Tier)
=======================================================
  Watching : ...\vault\Inbox
  Output   : ...\vault\Needs_Action
  Press Ctrl+C to stop
=======================================================
```

### 1B. Drop a test file

Open a second terminal:

```bash
echo "Q3 revenue: $4.2M, up 18% YoY" > vault/Inbox/quarterly_report.txt
```

### 1C. Verify task card

```bash
dir vault\Needs_Action\FILE_quarterly_report*
```

**Pass:** A file like `FILE_quarterly_report.txt_2026-02-14T...+05.md` exists.

Open it and confirm:
- YAML frontmatter has `type: file_drop`, `status: pending`, `priority: normal`
- Body says "New file detected in Inbox for processing"
- File size and timestamp are correct

### 1D. Verify Claude plan generation

```bash
dir vault\Plans\FILE_quarterly_report*
```

**Pass:** A `_PLAN.md` file exists with structured plan sections.
**Acceptable fail:** "Claude CLI not found" warning — task card still created.

### 1E. Cleanup

```bash
del vault\Inbox\quarterly_report.txt
```

Stop the watcher with `Ctrl+C`.

---

## Test 2 — Gmail Watcher → Task Card → Plan

### 2A. Prerequisites

- `watchers/credentials.json` exists (OAuth from Google Cloud Console)
- Gmail API enabled in your GCP project
- First-run auth completed (`watchers/gmail_token.json` exists)

If first run, the browser will open for OAuth consent.

### 2B. Start the watcher

```bash
python watchers/gmail_watcher.py
```

**Expected output:**
```
=======================================================
  AI Employee — Gmail Watcher (Silver Tier)
=======================================================
  Output   : ...\vault\Needs_Action
  Poll     : every 60s
  Press Ctrl+C to stop
=======================================================
```

### 2C. Send yourself a test email

From any email account, send to your configured Gmail:

```
To:      your.email@gmail.com
Subject: URGENT: Q4 budget approval needed
Body:    Please review the attached Q4 budget by Friday EOD.
         This is time sensitive.
```

### 2D. Wait one poll cycle (60s), then verify

```bash
dir vault\Needs_Action\EMAIL_*
```

**Pass:** A file like `EMAIL_URGENT- Q4 budget approval needed_2026-02-14T...+05.md` exists.

Open it and confirm:
- `type: email` and `source: gmail`
- `priority: high` (because subject contains "URGENT")
- `gmail_id:` is populated
- Preview snippet matches the email body

### 2E. Verify deduplication

Wait another poll cycle. Check the logs — you should see "No new unread emails" or the same email ID skipped.

**Pass:** No duplicate task card created.

### 2F. Verify Plan generation

```bash
dir vault\Plans\EMAIL_URGENT*
```

**Pass:** A `_PLAN.md` file with priority assessment, recommended actions.

Stop the watcher with `Ctrl+C`.

---

## Test 3 — Priority Detection

### 3A. Test high-priority keywords

Send yourself emails with these subjects (one at a time, wait for watcher to process):

| Subject                          | Expected Priority |
|----------------------------------|-------------------|
| `URGENT: server down`           | `high`            |
| `Action Required: renew license`| `high`            |
| `Meeting notes from Tuesday`    | `normal`          |
| `ASAP - client deliverable`     | `high`            |
| `Newsletter: weekly digest`     | `normal`          |

### 3B. Verify

Open each generated task card and check the `priority:` field in frontmatter.

**Pass:** All `high` keywords correctly detected. Non-urgent emails are `normal`.

---

## Test 4 — Full Email HITL Flow (End-to-End)

This is the critical Silver Tier flow:

```
Email arrives
  → Gmail watcher creates task card in Needs_Action/
    → Claude generates Plan.md
      → Plan recommends "send_email" (sensitive action)
        → Card moves to Pending_Approval/
          → Human reviews and moves to Approved/
            → Claude executes via MCP (email_mcp.js)
              → Email sent, logged to Sent_Emails/
```

### 4A. Simulate the flow manually

**Step 1 — Create a task card** (or reuse one from Test 2):

```bash
dir vault\Needs_Action\EMAIL_*
```

Pick one. Note the filename.

**Step 2 — Generate a plan** (if not auto-generated):

```bash
claude --print --prompt "You are the AI Employee. Read the task card at vault/Needs_Action/EMAIL_URGENT-_Q4_budget_approval_needed_2026-02-14T12-00-00+05.md and the skills file at vault/SKILLS.md. Generate a structured plan with YAML frontmatter."
```

Save output to `vault/Plans/`.

**Step 3 — Simulate approval gate:**

If the plan recommends a sensitive action (e.g., `send_email`), move the card:

```bash
move vault\Needs_Action\EMAIL_URGENT*.md vault\Pending_Approval\
```

Review the card in `Pending_Approval/`. If you approve:

```bash
move vault\Pending_Approval\EMAIL_URGENT*.md vault\Approved\
```

**Step 4 — Execute via MCP:**

Start Claude with MCP enabled and ask it to process the approved task:

```bash
claude
```

Then in Claude:

```
Read the approved task card in vault/Approved/ and execute
the recommended action. If it says to send an email reply,
use the draft_email tool first, show me the preview, and
wait for my approval before calling send_email.
```

### 4B. Verify MCP email sending

Claude should:
1. Call `draft_email` → show you a preview
2. Ask "Should I send this email?"
3. Only call `send_email` after your "yes"

**Pass checks:**
- [ ] Draft preview shown with correct To/Subject/Body
- [ ] No email sent without your explicit approval
- [ ] After approval, email actually arrives in recipient's inbox
- [ ] Log file created in `vault/Sent_Emails/SENT_*.md`
- [ ] Log contains correct metadata (to, subject, sent_at, message_id)

### 4C. Test MCP draft discard

```
Draft an email to test@example.com with subject "Test discard"
```

When the preview appears:

```
No, discard that draft.
```

**Pass:** Claude calls `discard_draft`. No email sent.

---

## Test 5 — WhatsApp Watcher

### 5A. First-run setup

```bash
python watchers/whatsapp_watcher.py
```

A Chromium window opens to WhatsApp Web. Scan the QR code with your phone.
Wait until "WhatsApp Web logged in successfully!" appears in the console.

### 5B. Trigger a message

From another phone or WhatsApp account, send a message to your number:

```
Hey, urgent: can you review the project proposal ASAP?
```

### 5C. Wait one poll cycle (30s), then verify

```bash
dir vault\Needs_Action\WHATSAPP_*
```

**Pass:** Task card exists with:
- `type: whatsapp_message`
- `priority: high` (contains "urgent" and "ASAP")
- Preview matches the message
- Sender name is correct

### 5D. Verify deduplication

Wait another cycle. Same message should not create a second card.

Stop with `Ctrl+C`. Session is saved in `watchers/.whatsapp_session/`.

---

## Test 6 — LinkedIn Poster

### 6A. First-run login

```bash
python watchers/linkedin_poster.py watch
```

Browser opens. Log into LinkedIn manually. Wait for "LinkedIn login detected!"
Press `Ctrl+C` to stop. Session saved in `watchers/.linkedin_session/`.

### 6B. Generate a draft via Claude

```bash
python watchers/linkedin_poster.py generate "AI agents in the workplace" --tone professional
```

**Pass:** Draft file created in `vault/LinkedIn_Drafts/DRAFT_AI agents*.md`.

### 6C. Review the draft

Open the draft file. Edit the content if desired. Confirm `status: draft`.

### 6D. Post via single-file mode

```bash
python watchers/linkedin_poster.py post "vault/LinkedIn_Drafts/DRAFT_AI agents in the workplace_2026-02-14T....md"
```

**Pass:**
- Preview shown in terminal
- Confirmation prompt appears (`Post this to LinkedIn? [y/N]`)
- On `y`: browser opens, content is typed, Post button clicked
- Log file created in `vault/LinkedIn_Posted/LI_posted_*.md`
- Draft status updated to `status: posted`

### 6E. Test scheduled drafts

```bash
python watchers/linkedin_poster.py generate "Remote work tips" --schedule 2026-12-31T10:00
```

Start the watcher:

```bash
python watchers/linkedin_poster.py watch
```

**Pass:** Log shows "Draft scheduled for 2026-12-31 — not yet time." Draft is skipped.

---

## Test 7 — Orchestrator

### 7A. Start the orchestrator

```bash
python orchestrator.py
```

**Expected output:**
```
============================================================
  AI Employee — Orchestrator (Silver Tier)
============================================================
  Project  : ...\AI Empolyee
  Vault    : ...\vault
  Logs     : ...\logs
  Time     : 2026-02-14 14:30:00 PKT
============================================================
```

### 7B. Verify watcher subprocess launch

```bash
# Check logs
type logs\filesystem_watcher.log
type logs\gmail_watcher.log
```

**Pass:** Both watchers started, PIDs logged. WhatsApp/LinkedIn show as "Disabled".

### 7C. Verify Dashboard update

```bash
type vault\Dashboard.md
```

**Pass:** Dashboard shows:
- Orchestrator: **Running**
- filesystem_watcher: **Running** (PID xxxx)
- gmail_watcher: **Running** (PID xxxx)
- whatsapp_watcher: Disabled
- linkedin_poster: Disabled
- Task counts are correct

### 7D. Test scheduled jobs manually

To test without waiting for the scheduled time, open a Python shell:

```python
import importlib, orchestrator as o
o.morning_briefing()        # Generates vault/Plans/DAILY_BRIEFING_2026-02-14.md
o.update_dashboard()        # Refreshes vault/Dashboard.md
o.vault_cleanup()           # Moves old completed cards to Done/
o.scheduled_linkedin_draft() # Generates a LinkedIn draft
```

**Pass:** Each function runs without error. Check the output files.

### 7E. Test watcher health check (crash recovery)

While orchestrator is running, manually kill one watcher:

```bash
# Find the watcher PID from logs or Dashboard.md
taskkill /PID <filesystem_watcher_pid>
```

Wait 5 minutes (or call `o.health_check_watchers()` in Python).

**Pass:** Orchestrator log shows "Watcher 'filesystem_watcher' is down... Restarting..."
and the watcher restarts with a new PID.

### 7F. Graceful shutdown

Press `Ctrl+C`.

**Pass:** All watchers terminated cleanly. Log shows "Orchestrator stopped. Goodbye!"

---

## Test 8 — PM2 Persistence

### 8A. Start with PM2

```bash
pm2 start ecosystem.config.js
```

### 8B. Verify running

```bash
pm2 status
```

**Pass:** Shows `ai-employee` with status `online`.

```bash
pm2 logs ai-employee --lines 20
```

**Pass:** Shows orchestrator startup logs, watcher PIDs.

### 8C. Test auto-restart

```bash
pm2 stop ai-employee
pm2 start ai-employee
```

**Pass:** Orchestrator restarts, watchers re-launch.

### 8D. Test crash recovery

```bash
# Kill the orchestrator process directly
taskkill /F /PID <orchestrator_pid>
```

Wait 5 seconds (PM2 `restart_delay`).

```bash
pm2 status
```

**Pass:** PM2 auto-restarts the orchestrator. Status back to `online`.

### 8E. Test boot persistence

```bash
pm2 startup
pm2 save
```

Restart your computer. After reboot:

```bash
pm2 status
```

**Pass:** `ai-employee` is running automatically.

### 8F. Check PM2 logs

```bash
type logs\pm2-out.log
type logs\pm2-error.log
```

**Pass:** Logs are clean, no unhandled errors.

---

## Test 9 — End-to-End Mega Flow

This test validates the complete Silver Tier pipeline in one shot.

### Setup

```bash
# Start everything via PM2
pm2 start ecosystem.config.js
```

### Trigger

1. **Send yourself an email** with subject "URGENT: Contract review deadline tomorrow"
2. **Drop a file** into `vault/Inbox/contract_v2.pdf`
3. **Send a WhatsApp** message (if enabled): "Hey, did you see the contract email?"

### Verify (within 2 minutes)

```bash
dir vault\Needs_Action\
```

**Expected files:**
- `EMAIL_URGENT- Contract review*.md` (priority: high)
- `FILE_contract_v2.pdf_*.md` (priority: normal)
- `WHATSAPP_*.md` (if enabled, priority: normal or high)

```bash
dir vault\Plans\
```

**Expected:** Matching `_PLAN.md` files for each task card.

### Execute MCP action

```bash
claude
```

In Claude:
```
Read the urgent email task card in vault/Needs_Action/ about the contract.
Draft a reply acknowledging receipt and confirming review by tomorrow.
```

Claude uses `draft_email` → you review → approve → `send_email` → logged.

### Final verification

```bash
dir vault\Sent_Emails\
type vault\Dashboard.md
```

**All-pass criteria:**
- [ ] Email task card created with `priority: high`
- [ ] File task card created with correct metadata
- [ ] Plans generated for each card
- [ ] Draft email preview shown before sending
- [ ] Email only sent after explicit approval
- [ ] Sent email logged to `vault/Sent_Emails/`
- [ ] Dashboard reflects current system state
- [ ] No duplicate task cards
- [ ] PM2 shows `online` status throughout

---

## Common Errors and Fixes

| # | Error | Cause | Fix |
|---|-------|-------|-----|
| 1 | `ModuleNotFoundError: No module named 'watchdog'` | Missing Python dependency | `pip install watchdog` |
| 2 | `ModuleNotFoundError: No module named 'schedule'` | Missing Python dependency | `pip install schedule` |
| 3 | `ModuleNotFoundError: No module named 'playwright'` | Missing Python dependency | `pip install playwright && playwright install chromium` |
| 4 | `ModuleNotFoundError: No module named 'google'` | Missing Google API libs | `pip install google-api-python-client google-auth-oauthlib` |
| 5 | `FileNotFoundError: credentials.json` | Gmail OAuth not set up | Download from Google Cloud Console → save to `watchers/credentials.json` |
| 6 | `Token has been expired or revoked` | Gmail token stale | Delete `watchers/gmail_token.json` and restart — browser opens for re-auth |
| 7 | `Claude CLI not found` | Claude not installed or not in PATH | Install Claude CLI. Task cards still work without it — plans won't generate. |
| 8 | `Claude timed out` | Slow API or large prompt | Increase `CLAUDE_TIMEOUT` in `.env` (default 120s) |
| 9 | `SMTP auth failed` | Wrong Gmail App Password | Generate a new one: Google Account → Security → App Passwords. Use 16-char code. |
| 10 | `GMAIL_ADDRESS required` (MCP) | MCP env vars not configured | Edit `.claude/mcp.json` — fill in real `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` |
| 11 | `SMTP connection refused` | 2FA not enabled on Gmail | Gmail App Passwords require 2-Step Verification. Enable it first. |
| 12 | Watcher fires twice for same file | OS-level double events | Normal on Bronze. Silver uses dedup hashes — safe to ignore. |
| 13 | Duplicate task cards appearing | State file corrupted | Delete `watchers/.gmail_processed_ids` or `.whatsapp_last_check.json` and restart |
| 14 | WhatsApp QR code not showing | Browser launch issue | Ensure Chromium installed: `playwright install chromium`. Use `headless=False`. |
| 15 | WhatsApp session expired | Cookie/session timeout | Delete `watchers/.whatsapp_session/` and re-scan QR code |
| 16 | LinkedIn login timeout | Session not established | Run `python watchers/linkedin_poster.py watch` standalone first to log in |
| 17 | LinkedIn post button not found | DOM selectors changed | LinkedIn updates their HTML — update selectors in `create_post()` |
| 18 | LinkedIn session lost | Cookie expiry | Delete `watchers/.linkedin_session/` and re-login |
| 19 | `pm2: command not found` | PM2 not installed | `npm install -g pm2` |
| 20 | PM2 won't auto-start on boot | Startup not configured | Run `pm2 startup` then `pm2 save` — follow the printed instructions |
| 21 | Orchestrator watcher keeps restarting | Underlying watcher error | Check `logs/<watcher_name>.log` for the real error |
| 22 | `Permission denied` on vault files | File locked by another process | Close any editors holding the file. On Windows, restart Explorer. |
| 23 | Task card has wrong timezone | System TZ mismatch | All times use PKT (UTC+05:00) hardcoded. Not a bug — by design. |
| 24 | `JSONDecodeError` in state file | Corrupt state | Delete the affected state file (`.gmail_processed_ids`, `.whatsapp_last_check.json`, or `.linkedin_state.json`) |
| 25 | Morning briefing not generating | Already exists for today | Check `vault/Plans/DAILY_BRIEFING_<today>.md` — delete it to force regeneration |
| 26 | Dashboard shows "Stopped" for a watcher | Watcher crashed between health checks | Wait for next health check (5 min) or restart the orchestrator |
| 27 | MCP `draft_email` returns error | MCP server not running | Ensure `.claude/mcp.json` is configured and Claude is started with MCP enabled |
| 28 | `vault/Sent_Emails/` log file missing | Directory doesn't exist | MCP creates it on first run. Or create manually: `mkdir vault\Sent_Emails` |
| 29 | Vault cleanup not moving cards | Cards don't have `status: completed` | Cleanup only moves cards with `status: completed` or `status: archived`. Update the card frontmatter. |
| 30 | Multiple orchestrator instances | PM2 + manual launch conflict | Stop one: `pm2 stop ai-employee` or `Ctrl+C` on the manual instance. Never run both. |

---

## Quick Health Check Script

Run this anytime to verify system state:

```bash
echo === Python Deps ===
python -c "import watchdog, schedule, playwright; print('OK')"

echo === Claude CLI ===
claude --version

echo === PM2 ===
pm2 status

echo === Vault Folders ===
dir /B vault\Needs_Action\
dir /B vault\Plans\
dir /B vault\Done\

echo === Orchestrator Log (last 10 lines) ===
powershell -command "Get-Content logs\orchestrator.log -Tail 10"

echo === Dashboard ===
type vault\Dashboard.md
```
