# Personal AI Employee — Bronze Tier

A local-first Personal AI Employee system that monitors your file system and automatically creates actionable task cards for AI processing.

## How It Works

```
You drop a file into vault/Inbox/
        ↓
Filesystem Watcher detects it instantly
        ↓
A task card (.md) is created in vault/Needs_Action/
        ↓
(Optional) Claude CLI generates an action plan in vault/Plans/
```

## Project Structure

```
AI Employee/
├── watchers/
│   └── filesystem_watcher.py   # Monitors Inbox for new files (Bronze)
├── vault/
│   ├── Inbox/                  # Drop files here
│   ├── Needs_Action/           # Auto-generated task cards appear here
│   ├── Company_Handbook.md     # Company policies (placeholder)
│   └── SKILLS.md               # AI Employee capabilities
├── .env                        # Secrets — never commit (gitignored)
├── .env.example                # Template for .env (safe to commit)
├── .gitignore
├── TESTING_GUIDE.md            # Bronze tier testing guide
└── README.md
```

## Prerequisites

- Python 3.10+
- [watchdog](https://pypi.org/project/watchdog/) — filesystem monitoring
- [Claude CLI](https://docs.anthropic.com) — AI plan generation (optional, degrades gracefully)

## Installation

```bash
pip install watchdog
```

## Usage

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

Drop any file into `vault/Inbox/` and a task card will appear in `vault/Needs_Action/` within seconds.

Stop with `Ctrl+C`.

## Quick Test

```bash
# Terminal 1 — start the watcher
python watchers/filesystem_watcher.py

# Terminal 2 — drop a test file
echo "Q3 revenue: $4.2M, up 18% YoY" > vault/Inbox/quarterly_report.txt

# Terminal 2 — verify (wait 2 seconds)
dir vault\Needs_Action\FILE_quarterly_report*
```

For the full testing guide see **[TESTING_GUIDE.md](TESTING_GUIDE.md)**.

## Tier Roadmap

| Tier   | Feature                                              | Status  |
|--------|------------------------------------------------------|---------|
| Bronze | Filesystem watcher + task card generation            | Done    |
| Silver | Gmail watcher + email task cards + Claude trigger    | Planned |
| Silver | WhatsApp watcher + message task cards                | Planned |
| Silver | Priority detection + deduplication                   | Planned |
| Silver | Email MCP server (HITL draft/send via Claude)        | Planned |
| Silver | Orchestrator + daily briefing + PM2 persistence      | Planned |
| Gold   | Full autonomous AI Employee with multi-channel input | Planned |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: No module named 'watchdog'` | `pip install watchdog` |
| `Claude CLI not found` | Install Claude CLI. Task cards still work — plans won't generate. |
| Watcher fires twice for same file | Normal OS-level double event — safe to ignore, no duplicate cards created. |
| `Permission denied` on vault files | Close any editors holding the file. On Windows, restart Explorer. |

## License

MIT
