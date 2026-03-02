# Lessons Learned — Personal AI Employee (Gold Tier)

> Retrospective template for the build. Fill in each section honestly.
> Pre-filled with observations from this project's actual architecture.

---

## Project Summary

| Field | Value |
|-------|-------|
| Project | Personal AI Employee |
| Tier achieved | Gold |
| Build duration | _fill in_ |
| Lines of code | ~3,500 Python + JS |
| External services integrated | Odoo 19, Gmail, Meta Graph, Twitter v2, LinkedIn |
| Claude integrations | CLI subprocess, MCP tools (odoo_mcp, email_mcp, meta_mcp, twitter_mcp) |

---

## What Worked Exceptionally Well

### 1. Frontmatter + Markdown as the Universal Data Format
Every task card, draft, plan, and briefing uses YAML frontmatter + Markdown body.
This made it trivially easy for Claude to read, write, and reason about state without
any database or binary format. Claude can read a task card and understand its full
context from a single file.

**Lesson:** When building AI systems, design your data format for Claude's natural
language strengths. YAML frontmatter is readable by both humans and Claude.

---

### 2. Signal Files as Completion Mechanism for Ralph Loop
Using filesystem signals (Claude writes a specific `.md` file when done) as the loop
termination condition was elegant. No polling, no shared state, no IPC.
The `ralph_loop.py` just checks `glob(done_glob)` after each iteration.

**Lesson:** Let the AI write its own completion signals. File existence is a simple,
reliable, auditable state machine.

---

### 3. AuditLogger "Never Crashes the Caller" Philosophy
Wrapping every write in `try/except pass` in `AuditLogger._write()` meant that logging
failures never cascaded into the actual business logic. The watcher kept running
even if the log directory was temporarily unavailable.

**Lesson:** Observability infrastructure must be more reliable than the system it observes.
Make logging fault-tolerant by design, not by accident.

---

### 4. CircuitBreaker as the Canonical "Odoo Is Down" Signal
Using a circuit breaker meant that instead of having every Odoo call independently
time out (5s × 5 retries = 25s per call, parallelized chaos), the entire system learned
"Odoo is down" after 5 failures and stopped hammering it. Recovery was clean and automatic.

**Lesson:** Circuit breakers are not just for microservices. They work great for
any system where a single dependency can be unreachable for minutes at a time.

---

### 5. HITL Drafts Prevented Every Potential Data Disaster
The pattern of "draft first, confirm only after human approval" for all Odoo write
operations meant that during development, no test invoices or payments accidentally
appeared in the live Odoo database. The draft files in `vault/Odoo_Drafts/` served
as a paper trail of everything Claude wanted to do but hadn't been allowed to yet.

**Lesson:** For any AI-generated write operation on financial or business-critical data,
always interpose a human review step. The cost is negligible. The safety gain is enormous.

---

## What Was Harder Than Expected

### 1. Process Supervision on Windows
The `sys.executable` subprocess model worked, but Windows doesn't have `SIGTERM` in
the same way Linux does. `proc.terminate()` sends `SIGTERM` on Unix but
`TerminateProcess()` on Windows — which is abrupt. The `timeout=10` in
`stop_all_watchers()` provides a grace period but may not allow watchers to clean up.

**Fix applied:** Structured logging via `AuditLogger` means the JSONL file is always
in a valid state (each line is an independent JSON object). Even a hard kill leaves
the log intact.

**Lesson:** Design for crash-resilience at the data layer, not just at the process layer.

---

### 2. Gmail OAuth Token Refresh in Long-Running Processes
The `google-auth` library's automatic token refresh works well for single requests
but the `gmail_watcher.py` keeps a single service object alive indefinitely.
After token expiry (typically 1 hour), the next poll fails silently.

**Fix applied:** The `@retry(service="gmail")` decorator catches the 401 and retries,
which triggers a fresh token refresh. But the underlying `service` object holds the
stale token. In production, consider re-calling `authenticate_gmail()` after auth errors.

**Lesson:** Never assume a long-lived API client will handle token rotation transparently.

---

### 3. Claude CLI Timeout Calibration
`subprocess.run(["claude", "--print", ...], timeout=120)` — 120 seconds is long for
interactive use but short for complex tasks. The morning briefing sometimes timed out
when Claude had many Needs_Action cards to summarize.

**Fix applied:** Increased to `timeout=180` for the briefing. Ralph Loop uses `Popen`
(non-blocking) for long tasks.

**Lesson:** Always instrument your Claude subprocess calls with timing metrics. You need
real data on p95 latency before setting timeouts.

---

### 4. MCP State Lost on Server Restart
The in-memory `drafts: dict` in `odoo_mcp.py` is wiped when the MCP server restarts.
This meant a draft created in one session was invisible in the next.

**Trade-off:** Re-loading drafts from `vault/Odoo_Drafts/` on startup would solve this
but would require parsing the vault files and reconstructing the `drafts` dict — adding
complexity. The HITL safety model (drafts are ephemeral by intent) actually makes this
acceptable.

**Lesson:** In-memory state in MCP servers must be treated as ephemeral.
If persistence is needed, write it to disk in the MCP itself.

---

### 5. Testing With Real External Services
You can't unit test Odoo invoice creation without a running Odoo. The circuit breaker
tests require either a mock or actually breaking Odoo. This made the test cycle slow.

**Lesson:** Design systems so the external service boundary is thin and injectable.
In `odoo_mcp.py`, `odoo_execute()` is the single point of external contact.
Mocking just that function would make all 11 tools testable in isolation.

---

## Architecture Decisions Worth Revisiting

### Decision 1: File-based queue vs. SQLite
We used JSON files in `vault/Queue/` for the offline queue. This is simple and human-readable
but has race conditions if multiple processes drain simultaneously.

**Alternative:** SQLite with a single-writer pattern, or a proper queue like Redis.

**When to revisit:** When queue depth regularly exceeds 100 items, or when concurrent
drain is needed from multiple processes.

---

### Decision 2: Subprocess `claude --print` vs. Anthropic API
Using the Claude CLI subprocess is convenient (no API key management in code) but adds
~200ms overhead per call and couples the system to the CLI version installed.

**Alternative:** Call the Anthropic API directly using `anthropic` Python package.
This would also enable streaming, better error handling, and model selection per task.

**When to revisit:** When the system needs to run in a CI/CD pipeline or container
where the Claude CLI can't be installed.

---

### Decision 3: Single orchestrator vs. distributed tasks
Everything runs in one Python process (the orchestrator). This is simple but means
a slow `morning_briefing()` can block `vault_cleanup()` if they overlap.

**Alternative:** `concurrent.futures.ProcessPoolExecutor` or `asyncio` for true parallelism.

**When to revisit:** When tasks start overlapping visibly in the logs.

---

## What I Would Do Differently

1. **Write the `AuditLogger` first** — before writing a single watcher. Having structured
   logs from day one would have made every debugging session faster.

2. **Add a `--dry-run` flag to every watcher** — lets you see what it *would* do
   without actually writing task cards. Essential for testing new keyword detection rules.

3. **Use environment variables consistently** — some configs are hardcoded (poll intervals,
   paths), some are env vars (Odoo creds). A single `config.py` loading from `.env`
   would have been cleaner.

4. **Add a simple health dashboard endpoint** — `vault/Dashboard.md` is good but a
   `python watchdog.py --status` command that prints a one-page summary would be more useful.

5. **Test the offline queue drain from day one** — the queue enqueue path was tested
   implicitly when Odoo was down, but the drain path was never called in a real scenario.
   Write the drain executor before you need it in production.

---

## Metrics

_Fill in with your actual numbers:_

| Metric | Value |
|--------|-------|
| Total files monitored | _e.g., 147 emails processed_ |
| Task cards created | |
| Plans generated | |
| Odoo invoices created via MCP | |
| CEO Briefings generated | |
| Watchdog restarts observed | |
| Circuit breaker trips | |
| Offline queue items successfully drained | |
| Claude API calls (estimate) | |

---

## What to Build Next (Gold+ Ideas)

- **Telegram bot** as a human-in-the-loop notification channel — send draft approval
  requests to Telegram, receive approval replies back
- **Scheduled report emails** — use email_mcp to send the CEO Briefing as an email at 06:00 Monday
- **Odoo queue drain scheduler** — auto-drain when circuit closes rather than manually
- **Multi-tenant support** — one orchestrator, multiple vault/ directories (clients)
- **MCP for Google Calendar** — schedule meetings, create reminders from task cards
- **LLM-based priority scoring** — replace keyword detection with Claude-based email
  classification in the gmail_watcher
