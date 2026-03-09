# Bronze Tier — Testing Guide

> Step-by-step validation of the Filesystem Watcher.
> Run each test in order. Every step includes the exact command, what to
> observe, and what a pass/fail looks like.

---

## Prerequisites Checklist

Before testing, confirm every dependency is in place:

```bash
# Python package
pip install watchdog

# Claude CLI — verify it exists (optional)
claude --version
```

**Folder scaffold** — ensure these exist:

```
vault/Inbox/
vault/Needs_Action/
```

---

## Test 1 — Filesystem Watcher (Basic)

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

**Pass:** Watcher starts without errors.
**Fail:** `ModuleNotFoundError` → run `pip install watchdog`.

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

### 1D. Verify Claude plan generation (optional)

```bash
dir vault\Plans\FILE_quarterly_report*
```

**Pass:** A `_PLAN.md` file exists with structured plan sections.
**Acceptable:** "Claude CLI not found" warning — task card is still created correctly.

### 1E. Cleanup

```bash
del vault\Inbox\quarterly_report.txt
```

Stop the watcher with `Ctrl+C`.

---

## Test 2 — Priority Detection

### 2A. Drop a high-priority file

```bash
echo "URGENT: server is down" > vault/Inbox/urgent_alert.txt
```

### 2B. Verify priority

Open the generated task card in `vault/Needs_Action/`.

**Pass:** `priority: high` in the YAML frontmatter.

### 2C. Drop a normal-priority file

```bash
echo "Weekly newsletter content" > vault/Inbox/newsletter.txt
```

**Pass:** `priority: normal` in the YAML frontmatter.

### 2D. Cleanup

```bash
del vault\Inbox\urgent_alert.txt
del vault\Inbox\newsletter.txt
```

---

## Test 3 — Multiple Files

### 3A. Drop several files at once

```bash
echo "Report A" > vault/Inbox/report_a.txt
echo "Report B" > vault/Inbox/report_b.txt
echo "Report C" > vault/Inbox/report_c.txt
```

### 3B. Verify all task cards created

```bash
dir vault\Needs_Action\FILE_report*
```

**Pass:** Three separate task cards exist, one for each file.

### 3C. Cleanup

```bash
del vault\Inbox\report_a.txt
del vault\Inbox\report_b.txt
del vault\Inbox\report_c.txt
```

Stop the watcher with `Ctrl+C`.

---

## Test 4 — End-to-End Quick Flow

```bash
# Terminal 1 — start the watcher
python watchers/filesystem_watcher.py

# Terminal 2 — drop a test file
echo "test content" > vault/Inbox/sample_report.txt

# Terminal 2 — verify (wait 2 seconds)
dir vault\Needs_Action\FILE_sample_report*
dir vault\Plans\FILE_sample_report*
```

**All-pass criteria:**
- [ ] Task card created in `vault/Needs_Action/` within 2 seconds
- [ ] YAML frontmatter is valid (`type`, `status`, `priority` fields present)
- [ ] File name and size are correct in the card
- [ ] Plan generated in `vault/Plans/` (if Claude CLI is installed)
- [ ] No duplicate task cards on repeated runs

---

## Common Errors and Fixes

| # | Error | Cause | Fix |
|---|-------|-------|-----|
| 1 | `ModuleNotFoundError: No module named 'watchdog'` | Missing dependency | `pip install watchdog` |
| 2 | `Claude CLI not found` | Claude not installed or not in PATH | Install Claude CLI. Task cards still work without it. |
| 3 | `Claude timed out` | Slow API response | Increase `CLAUDE_TIMEOUT` in `.env` (default 120s) |
| 4 | Watcher fires twice for same file | OS-level double event | Normal behaviour — no duplicate cards are created |
| 5 | `Permission denied` on vault files | File locked by another process | Close any editors holding the file. On Windows, restart Explorer. |
| 6 | Task card has wrong timezone | System TZ mismatch | All times use PKT (UTC+05:00) hardcoded — by design |
| 7 | Watcher exits immediately | Python version issue | Use Python 3.10+ |

---

## Quick Health Check

```bash
echo === Python Deps ===
python -c "import watchdog; print('OK')"

echo === Claude CLI ===
claude --version

echo === Vault Folders ===
dir /B vault\Needs_Action\
```
