# DEMO_GUIDE.md — Platinum Tier
# Screen Recording + Narration Guide for the AI Employee Demo

## Overview

This guide covers how to record a professional demo of the end-to-end AI Employee
pipeline using only free tools. The demo script (`scripts/demo_e2e.py`) runs
10 phases; each phase is mapped to a narration cue below.

---

## 1. Free Screen Recording Tools

### Windows (recommended: Windows Terminal + ShareX)

| Tool | Cost | Best For |
|------|------|----------|
| **ShareX** | Free/open-source | Full recording + cursor highlight + annotation |
| **OBS Studio** | Free/open-source | Livestream-quality recording, scene switching |
| **Xbox Game Bar** | Built-in (Win+G) | Quick one-click capture, no install needed |

**ShareX setup (quickest for demos):**
1. Download from <https://getsharex.com>
2. Capture → Screen Recording → select region (your terminal window)
3. Enable "Show cursor" and "Show keystroke labels" in After Capture settings
4. Output: MP4 (H.264), 1080p or 1440p

**OBS Studio setup:**
1. Add Source → Window Capture → select your terminal
2. Set Output → Recording → Format: MKV → Remux to MP4 after
3. Enable "High Quality, Medium File Size" preset

### macOS

| Tool | Notes |
|------|-------|
| **QuickTime Player** | Built-in; File → New Screen Recording |
| **OBS Studio** | Same as Windows; best quality |

**QuickTime:** Cmd+Shift+5 → select "Record Selected Portion" → choose terminal.

### Linux

| Tool | Notes |
|------|-------|
| **OBS Studio** | Best quality |
| **SimpleScreenRecorder** | Lightweight; `sudo apt install simplescreenrecorder` |
| **Kazam** | GUI simple; `sudo apt install kazam` |

---

## 2. Terminal Setup for Clean Recording

A cluttered terminal ruins a demo. Configure before hitting record:

### Font & Size
```
Font:  JetBrains Mono / Cascadia Code / Fira Code  (ligature fonts look great)
Size:  18–20 pt minimum for 1080p; 22 pt for 4K
```

### Window Size
```
Columns: 120  (fits all demo output without wrapping)
Rows:    35   (avoids scroll during a single phase)
```

### Color Theme
Use a high-contrast dark theme: **One Dark**, **Dracula**, or **Catppuccin Mocha**.
Avoid light themes — they wash out on video compression.

### Windows Terminal JSON snippet
```json
{
  "profiles": {
    "defaults": {
      "font": { "face": "Cascadia Code", "size": 18 },
      "colorScheme": "One Half Dark",
      "padding": "12",
      "scrollbarState": "hidden"
    }
  }
}
```

### Before Recording
- [ ] Close browser tabs and notifications (Do Not Disturb on)
- [ ] Hide taskbar / dock for a cleaner frame
- [ ] `cd "D:\Heck ---0\AI Empolyee"` — start from project root
- [ ] Run `cls` or `clear` to blank the terminal
- [ ] Zoom ShareX/OBS to the terminal window only (not full desktop)

---

## 3. Before-Demo Checklist

Run these steps **before** starting the recording:

```bash
# 1. Verify vault structure exists
python scripts/setup_vault_structure.py

# 2. Check .env.local has Gmail credentials
#    GMAIL_ADDRESS=you@gmail.com
#    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   (16-char App Password)

# 3. Safe dry-run first (no email sent, no git push)
python scripts/demo_e2e.py --dry-run --no-claude --no-git --auto-approve

# 4. Verify rate limiter is clear
python rate_limiter.py --status

# 5. Confirm Claude CLI is authenticated (if using live Claude)
claude --version

# 6. Clean up any leftover test files
python scripts/demo_e2e.py --from-phase 10   # jump straight to cleanup phase
```

### Gmail App Password Setup (one-time)
1. Google Account → Security → 2-Step Verification → App passwords
2. App name: "AI Employee" → Generate → copy 16-char password
3. Paste into `.env.local` as `GMAIL_APP_PASSWORD`
4. Test: `python scripts/demo_send_email.py --dry-run`

---

## 4. Recommended Demo Commands

### Safe first run (no real email, no git)
```bash
python scripts/demo_e2e.py \
    --dry-run \
    --no-claude \
    --no-git \
    --auto-approve
```

### Live demo with fixture draft (no Claude API needed)
```bash
python scripts/demo_e2e.py \
    --no-claude \
    --no-git \
    --auto-approve
```

### Full live demo (Claude drafts, Gmail sends)
```bash
python scripts/demo_e2e.py \
    --no-git \
    --to reviewer@yourdomain.com
```
*(Remove `--no-git` if git remote is configured.)*

### Interactive (pauses at each phase — good for narrated video)
```bash
python scripts/demo_e2e.py --no-claude --no-git
```
Pause at each `↵  Press ENTER to Continue...` prompt to narrate.

---

## 5. Phase-by-Phase Narration Script

Use this script when recording. Each cue maps to a demo_e2e.py phase header.

---

### INTRO (before Phase 1)

> "This is a demonstration of the Personal AI Employee system — a fully autonomous
> delegation pipeline that runs on a free Gmail account, a free Claude API key,
> and no paid services. I'll walk through the complete flow: an email arrives while
> the local machine is offline, the cloud VM drafts a reply, the vault syncs,
> and the local machine approves and sends — all logged and audited.
> Let's start."

---

### Phase 1 — Offline Simulation

**Screen:** `[Phase 1] Going offline...` banner + marker file created

> "Phase 1: the local orchestrator goes offline — simulating a laptop that's
> asleep or unreachable. The system writes an offline marker file so cloud
> components know not to wait for local confirmation."

---

### Phase 2 — Email Arrives

**Screen:** Task card JSON written to `vault/Needs_Action/email/`

> "Phase 2: an inbound email arrives from Sarah Chen, an operations manager asking
> about AI automation for her logistics team. The gmail_watcher would normally
> detect this — here we simulate it by writing a structured task card to the
> Needs_Action folder. Priority is high."

---

### Phase 3 — Cloud Draft

**Screen:** Claude generating reply (or fixture text streaming in)

> "Phase 3: the cloud VM's social_drafter — or in this case the email reply
> drafter — calls Claude to generate a professional reply. Notice it references
> the specific pain point Sarah mentioned: 3 hours a day copying supplier
> emails into their ERP. No generic template — it's context-aware.
> This runs entirely on the cloud VM, no local machine needed."

---

### Phase 4 — Approval File Written

**Screen:** JSON file written to `vault/Pending_Approval/email/`

> "Phase 4: the draft and metadata are written to the Pending Approval folder.
> This is the HITL — Human In The Loop — checkpoint. Nothing gets sent yet.
> The approval file contains the draft body, recipient, subject, and a priority
> field. An update event is also published to vault/Updates/ so the Dashboard
> will reflect this activity after the next sync."

---

### Phase 5 — Vault Sync

**Screen:** git push/pull output or file-copy simulation

> "Phase 5: vault sync. The cloud VM commits and pushes the Pending Approval file
> to a private Git repository. The local machine pulls it down. This is the
> only network operation in the entire pipeline — everything else is local file
> operations. If you're running with --no-git, this is simulated with a direct
> file copy."

---

### Phase 6 — Local Comes Online

**Screen:** Offline marker removed, `Local machine is ONLINE` message

> "Phase 6: the local machine comes back online. The offline marker is removed.
> The approval_watcher daemon — which was paused — now picks up the pending file
> and begins processing."

---

### Phase 7 — Approval Watcher Claims the File

**Screen:** `claim-by-move` atomic rename, `In_Progress/approval_watcher/` path

> "Phase 7: the approval watcher uses an atomic file rename — claim-by-move — to
> prevent double-processing. Even if two agents are running simultaneously, only
> one can win the rename. The file sits in In_Progress while the decision is made."

---

### Phase 8 — Human Approval

**Screen:** Either auto-approve banner or interactive `[A]pprove / [R]eject / [S]kip` prompt

> "Phase 8: the human approval step. In a real deployment, the business owner
> would review the draft in the Pending Approval folder and type 'approve' or
> 'reject'. For this demo, auto-approve is enabled. The file is moved to
> vault/Approved/email/ — the green light for sending."

*(If running interactively: pause here, type `a`, then continue.)*

---

### Phase 9 — Execute Send

**Screen:** Gmail SMTP connecting, `Sent to: Sarah Chen <sarah.chen@...>  ✓`

> "Phase 9: the email is sent via Gmail's free SMTP service — no SendGrid,
> no Mailgun, no paid API. Standard SMTP SSL on port 465, authenticated with
> a Google App Password. The rate limiter is checked first — max 20 sends per
> hour — then the email goes out. You'd see it arrive in a real inbox within
> seconds."

*(If using --dry-run: "In dry-run mode, the SMTP connection is skipped — notice
it shows exactly what would be sent without actually sending.")*

---

### Phase 10 — Audit + Done

**Screen:** Task card moved to Done/, audit log entry, rate counter

> "Phase 10: cleanup and audit. The task card is moved from Needs_Action to Done.
> The approval file is archived in Done/email/. An audit entry is written to
> vault/Logs/ in structured JSONL format — timestamp, component, action, recipient,
> subject — so every send is traceable. The rate counter is incremented.
> That's the complete pipeline."

---

### OUTRO

> "This entire flow — from email arrival to sent reply — ran on free infrastructure:
> Gmail SMTP, a Git repository, Claude's free tier, and local Python. No cloud
> database, no paid email service, no SaaS subscriptions. The same architecture
> scales to Odoo ERP integration, LinkedIn posting, and WhatsApp — all gated
> behind the same human-in-the-loop approval system. Thanks for watching."

---

## 6. Post-Recording Tips

### Trim & Export
- Cut the intro setup (cd, clear) — start from the Phase 1 banner
- Add chapter markers at each phase for YouTube/Loom navigation
- Export: MP4, H.264, 1080p60 — keeps file size reasonable (~100MB for a 5-min demo)

### Annotations (ShareX / OBS)
- Highlight the approval file path when it appears (yellow box)
- Zoom in (2×) on the `Sent to: ... ✓` line in Phase 9
- Show a split view: terminal on left, `vault/` folder tree on right (optional)

### Subtitles
- ShareX can auto-generate a keystroke overlay (shows keys pressed)
- For captions: paste narration script into YouTube auto-subtitle editor and correct

### Loom / Streamable (for sharing)
```
Loom Free:  5 min limit — fine for a focused demo
Streamable: unlimited uploads, no account required
YouTube:    unlisted link — best for full walkthroughs
```

---

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set` | Set in `.env.local`; verify with `python config.py` |
| `SMTPAuthenticationError` | App Password must be 16 chars, no spaces; not your Gmail password |
| `No approved drafts found` | Run phases 1–8 first, or use `--from-phase 9` only after approval exists |
| `RateLimitError: email_send` | `python rate_limiter.py --reset email_send` |
| `vault/Approved/email/ not found` | `python scripts/setup_vault_structure.py` |
| Phase skipped (no Claude API) | Add `--no-claude` flag to use fixture draft |
| git push fails | Add `--no-git` flag; configure remote with `git remote add origin <url>` |
| Windows terminal colors missing | Set `FORCE_COLOR=1` in env, or use Windows Terminal (not cmd.exe) |
| Demo log location | `vault/Logs/demo_e2e.log` — check for per-phase error details |

---

*Generated for the Platinum Tier AI Employee project. All tools used are free and open-source.*
