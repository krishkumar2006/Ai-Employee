"""
Ralph Wiggum Stop Hook Loop — Gold Tier
=========================================
Named after Ralph Wiggum ("I'm helping!") — keeps Claude autonomously
working on a multi-step task until a real completion signal is met.

WHY THIS EXISTS
───────────────
A single `claude --print` call has a context limit. Complex tasks like
"process all 12 Needs_Action cards, write plans, classify emails, then
run the full CEO audit" need multiple Claude invocations. Without this
wrapper, you'd have to babysit every step.

Ralph Loop solves this: you describe the full task once, declare how
you'll know it's done (the "completion promise"), and Ralph keeps
feeding Claude the next batch until the job is finished.

TWO OPERATING MODES
────────────────────

MODE 1 — External Loop  (orchestrator / PM2 / CLI / Windows Task Scheduler)
  ralph_loop.py manages multiple `claude --print` subprocesses.
  Each call processes one "batch" of work. Between calls ralph_loop.py
  checks the completion signal. Loop ends when signal is satisfied.

    python ralph_loop.py \\
      --task "Process all Needs_Action cards: read each, classify as
              actionable or informational, create a Plan.md for each
              actionable item, update status in frontmatter to 'handled',
              and write vault/Plans/NEEDS_ACTION_COMPLETE_<today>.md
              when all are processed." \\
      --done-type signal_file \\
      --done-glob "vault/Plans/NEEDS_ACTION_COMPLETE_*.md" \\
      --label needs-action-audit \\
      --max-iter 15 \\
      --batch 4

MODE 2 — Stop Hook  (active Claude Code interactive sessions)
  Called from .claude/settings.json Stop hook AFTER every Claude response.
  Checks the shared state file. If task is incomplete → exits code 2,
  which tells Claude Code "do NOT stop — keep going". The hook's stdout
  becomes Claude's new user-turn context, driving the next iteration
  with no human input required.

    python ralph_loop.py --hook-check

  Configured in .claude/settings.json:
    "Stop": [{"hooks": [{"type": "command",
               "command": "python ralph_loop.py --hook-check"}]}]

COMPLETION SIGNAL TYPES
────────────────────────
  --done-type signal_file  + --done-glob "vault/Plans/DONE_*.md"
      Claude writes a specific file when it considers itself done.
      Most explicit — Claude decides completion, you just verify the file.

  --done-type empty_dir    + --done-path "vault/Needs_Action"
      Directory has zero .md files left (all moved/archived).
      Good for "drain the queue" tasks.

  --done-type all_handled  + --done-path "vault/Needs_Action"
      Every .md file in the directory has "status: handled",
      "status: archived", or "status: done" in its frontmatter.
      Good when files stay but get marked complete.

  --done-type file_count   + --done-path "vault/Done" + --done-count 10
      At least N files exist in a directory.
      Good for "generate 10 social media posts" tasks.

STATE FILE: .ralph_state.json  (project root)
LOG OUTPUT: vault/Plans/RALPH_LOG_<label>_<date>.md

Part of the Personal AI Employee system.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
import uuid
import argparse
import logging

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
VAULT_PATH   = PROJECT_ROOT / "vault"
PLANS_PATH   = VAULT_PATH / "Plans"
NEEDS_ACTION = VAULT_PATH / "Needs_Action"
DONE_PATH    = VAULT_PATH / "Done"
STATE_FILE   = PROJECT_ROOT / ".ralph_state.json"

# Pakistan Standard Time (UTC+05:00)
PKT = timezone(timedelta(hours=5))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ralph_loop — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "ralph_loop.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("ralph_loop")


# ===========================================================================
# State Management
# ===========================================================================

def _now() -> datetime:
    return datetime.now(tz=PKT)

def _now_iso() -> str:
    return _now().isoformat()

def _now_str() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S PKT")

def load_state() -> dict[str, Any]:
    """Load current loop state from .ralph_state.json."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Corrupt state file — starting fresh.")
    return {}

def save_state(state: dict[str, Any]) -> None:
    """Persist loop state."""
    try:
        STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        log.error("Failed to save state: %s", e)

def clear_state() -> None:
    """Remove state file (called when loop ends)."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()


# ===========================================================================
# Completion Signal Checkers
# ===========================================================================

def check_signal_file(done_glob: str) -> tuple[bool, str]:
    """True if a file matching the glob pattern exists in VAULT_PATH."""
    # Support both absolute and vault-relative paths
    glob_path = Path(done_glob)
    if not glob_path.is_absolute():
        glob_path = PROJECT_ROOT / done_glob

    # Handle glob patterns
    parent = glob_path.parent
    pattern = glob_path.name

    if not parent.exists():
        return False, f"Directory {parent} does not exist yet"

    matches = list(parent.glob(pattern))
    if matches:
        return True, f"Signal file found: {matches[0].name}"
    return False, f"Waiting for: {done_glob} (0 matches)"


def check_empty_dir(done_path: str) -> tuple[bool, str]:
    """True if directory has no .md files remaining."""
    path = Path(done_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / done_path

    if not path.exists():
        return True, f"Directory {done_path} does not exist (treating as empty)"

    remaining = list(path.glob("*.md"))
    if not remaining:
        return True, f"Directory is empty: {done_path}"
    return False, f"{len(remaining)} .md files remain in {done_path}"


def check_all_handled(done_path: str) -> tuple[bool, str]:
    """True if every .md file in the directory has a 'done' status in frontmatter."""
    path = Path(done_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / done_path

    if not path.exists():
        return True, f"Directory {done_path} does not exist"

    files = list(path.glob("*.md"))
    if not files:
        return True, "No files to check"

    done_statuses = {"handled", "archived", "done", "completed", "posted",
                     "processed", "skipped", "no_action"}

    unhandled = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            status = None
            for line in text.splitlines():
                if line.strip().startswith("status:"):
                    status = line.split(":", 1)[1].strip().lower()
                    break
            if status not in done_statuses:
                unhandled.append(f"{f.name} (status: {status or 'not set'})")
        except Exception:
            unhandled.append(f"{f.name} (unreadable)")

    if not unhandled:
        return True, f"All {len(files)} files are handled"
    return False, f"{len(unhandled)} files not yet handled: {', '.join(unhandled[:3])}"


def check_file_count(done_path: str, required_count: int) -> tuple[bool, str]:
    """True if at least required_count .md files exist in the directory."""
    path = Path(done_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / done_path

    if not path.exists():
        return False, f"Directory {done_path} does not exist"

    count = len(list(path.glob("*.md")))
    if count >= required_count:
        return True, f"Found {count} files (needed {required_count})"
    return False, f"{count}/{required_count} files exist in {done_path}"


def is_done(state: dict[str, Any]) -> tuple[bool, str]:
    """Check if the completion signal for the current task is satisfied."""
    done_type = state.get("done_type", "signal_file")

    if done_type == "signal_file":
        return check_signal_file(state.get("done_glob", ""))

    elif done_type == "empty_dir":
        return check_empty_dir(state.get("done_path", "vault/Needs_Action"))

    elif done_type == "all_handled":
        return check_all_handled(state.get("done_path", "vault/Needs_Action"))

    elif done_type == "file_count":
        return check_file_count(
            state.get("done_path", "vault/Done"),
            int(state.get("done_count", 1)),
        )

    return False, f"Unknown done_type: {done_type}"


# ===========================================================================
# Vault Context Helpers (injected into continuation prompts)
# ===========================================================================

def count_remaining(state: dict[str, Any]) -> dict[str, Any]:
    """Return counts of pending/handled items for prompt context."""
    done_type = state.get("done_type")
    done_path = state.get("done_path", "")

    ctx: dict[str, Any] = {}

    if done_path:
        path = Path(done_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / done_path

        if path.exists():
            all_files  = list(path.glob("*.md"))
            done_stats = {"handled", "archived", "done", "completed",
                          "processed", "skipped", "no_action", "posted"}
            handled    = []
            pending    = []

            for f in all_files:
                try:
                    text = f.read_text(encoding="utf-8")
                    status = None
                    for line in text.splitlines():
                        if line.strip().startswith("status:"):
                            status = line.split(":", 1)[1].strip().lower()
                            break
                    if status in done_stats:
                        handled.append(f.name)
                    else:
                        pending.append(f.name)
                except Exception:
                    pending.append(f.name)

            ctx["total"]    = len(all_files)
            ctx["handled"]  = len(handled)
            ctx["pending"]  = len(pending)
            ctx["pending_names"] = pending[:10]  # First 10 for prompt

    return ctx


# ===========================================================================
# Prompt Builder
# ===========================================================================

INITIAL_PROMPT_TEMPLATE = """\
You are the AI Employee Gold Tier operating in AUTONOMOUS LOOP MODE.

A multi-step task has been started by the orchestrator. You must work through
it systematically without waiting for human input at each step.

════════════════════════════════════════════════════════════
TASK LABEL:  {label}
STARTED AT:  {started_at}
ITERATION:   1 of {max_iterations} maximum
BATCH SIZE:  Process up to {batch_size} items this iteration
════════════════════════════════════════════════════════════

YOUR TASK:
{task}

════════════════════════════════════════════════════════════
COMPLETION PROMISE
When this entire task is fully complete, you MUST write the completion
signal so the loop knows to stop:

  {done_instruction}

This is not optional. The loop will keep running until this signal exists.
════════════════════════════════════════════════════════════

AUTONOMOUS OPERATION RULES:
1. Use your Read/Write/Edit tools to examine and update vault files directly.
2. Process items in batches of {batch_size}. Do not try to do everything at once.
3. After processing each item, update its `status:` frontmatter field immediately.
   Valid statuses: handled, archived, done, in_progress, skipped, no_action
4. If an item truly cannot be processed (corrupt file, external dependency),
   set status: skipped and note the reason. Never let one bad item block the rest.
5. Do not ask for confirmation. Make decisions and act on them.
6. At the end of this iteration, output a brief progress report in this format:
   RALPH_PROGRESS: processed=N, remaining=M, errors=K, notes=<short note>
   This line is parsed by the loop manager.

VAULT STRUCTURE:
  vault/Needs_Action/  — incoming task cards (to be processed)
  vault/Done/          — completed cards (move here when done + status: archived)
  vault/Plans/         — output plans, briefings, summaries
  vault/Business_Goals.md — company targets and rules

BEGIN WORK NOW. Process the first {batch_size} items.
"""

CONTINUATION_PROMPT_TEMPLATE = """\
You are the AI Employee Gold Tier continuing an autonomous multi-step task.

════════════════════════════════════════════════════════════
TASK LABEL:  {label}
ITERATION:   {iteration} of {max_iterations} maximum
ELAPSED:     {elapsed}
════════════════════════════════════════════════════════════

YOUR TASK (same as before):
{task}

════════════════════════════════════════════════════════════
PROGRESS SO FAR:
{progress_summary}
════════════════════════════════════════════════════════════

REMAINING WORK:
{remaining_summary}

════════════════════════════════════════════════════════════
COMPLETION PROMISE — still required:
{done_instruction}
════════════════════════════════════════════════════════════

INSTRUCTIONS FOR THIS ITERATION:
- Process the next {batch_size} PENDING items (skip already-handled ones).
- Use Read tool to check `status:` in frontmatter before processing each file.
- If a file already has status: handled/archived/done → skip it and move on.
- After processing, output:
  RALPH_PROGRESS: processed=N, remaining=M, errors=K, notes=<short note>

Continue now. Work through the next batch.
"""


def _done_instruction(state: dict[str, Any]) -> str:
    """Human-readable instruction for Claude about the completion signal."""
    done_type = state.get("done_type", "signal_file")

    if done_type == "signal_file":
        glob = state.get("done_glob", "vault/Plans/TASK_COMPLETE_*.md")
        # Convert glob pattern to an actual filename for today
        today = _now().strftime("%Y-%m-%d")
        filename = glob.replace("*", today)
        return (
            f"Write the file: {filename}\n"
            f"   Content: 'status: done\\ncompleted_at: {today}\\ntask: {state.get('label', 'task')}'\n"
            f"   The loop checks for this glob pattern: {glob}"
        )
    elif done_type == "empty_dir":
        p = state.get("done_path", "vault/Needs_Action")
        return (
            f"Ensure {p} contains zero .md files.\n"
            f"   Move each processed file to vault/Done/ after setting status: archived."
        )
    elif done_type == "all_handled":
        p = state.get("done_path", "vault/Needs_Action")
        return (
            f"Set status: handled (or archived/done) on every .md file in {p}.\n"
            f"   The loop checks that ALL files in that directory have a done status."
        )
    elif done_type == "file_count":
        p = state.get("done_path", "vault/Done")
        n = state.get("done_count", 1)
        return f"Ensure at least {n} .md files exist in {p}."

    return "Write vault/Plans/TASK_COMPLETE.md with status: done"


def build_initial_prompt(state: dict[str, Any]) -> str:
    return INITIAL_PROMPT_TEMPLATE.format(
        label=state["label"],
        started_at=state["started_at"],
        max_iterations=state["max_iterations"],
        batch_size=state.get("batch_size", 4),
        task=state["task"],
        done_instruction=_done_instruction(state),
    )


def build_continuation_prompt(state: dict[str, Any]) -> str:
    iteration   = state.get("iteration", 2)
    started     = datetime.fromisoformat(state["started_at"])
    elapsed_sec = (_now() - started).seconds
    elapsed     = f"{elapsed_sec // 60}m {elapsed_sec % 60}s"

    # Build progress summary from saved outputs
    outputs = state.get("outputs", [])
    if outputs:
        progress_lines = []
        for o in outputs[-3:]:  # Last 3 iterations
            progress_lines.append(
                f"  Iteration {o['iteration']}: {o.get('progress_note', '(no note)')} "
                f"({o.get('chars', 0)} chars output)"
            )
        progress_summary = "\n".join(progress_lines)
    else:
        progress_summary = "  (no previous iterations logged)"

    # Build remaining work summary
    ctx = count_remaining(state)
    if ctx:
        remaining_summary = (
            f"Total items: {ctx.get('total', '?')}\n"
            f"  Already handled: {ctx.get('handled', 0)}\n"
            f"  Still pending:   {ctx.get('pending', '?')}\n"
        )
        pending_names = ctx.get("pending_names", [])
        if pending_names:
            remaining_summary += "  Pending files:\n"
            for name in pending_names:
                remaining_summary += f"    - {name}\n"
    else:
        remaining_summary = "  (cannot determine — check vault/Needs_Action/ directly)"

    return CONTINUATION_PROMPT_TEMPLATE.format(
        label=state["label"],
        iteration=iteration,
        max_iterations=state["max_iterations"],
        elapsed=elapsed,
        task=state["task"],
        progress_summary=progress_summary,
        remaining_summary=remaining_summary,
        done_instruction=_done_instruction(state),
        batch_size=state.get("batch_size", 4),
    )


# ===========================================================================
# Claude Invocation
# ===========================================================================

def run_claude(prompt: str, timeout: int = 300, cwd: Path = VAULT_PATH) -> tuple[int, str]:
    """
    Run `claude --print -p <prompt>` and return (exit_code, stdout).
    Writes prompt to a temp file to avoid Windows command-line length limits.
    """
    prompt_file = PROJECT_ROOT / ".ralph_prompt_tmp.txt"
    try:
        prompt_file.write_text(prompt, encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        import sys as _sys
        _claude_cmd = "claude.cmd" if _sys.platform == "win32" else "claude"
        result = subprocess.run(
            [_claude_cmd, "--print", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            shell=(_sys.platform == "win32"),
            env=env,
        )
        return result.returncode, result.stdout.strip()

    except subprocess.TimeoutExpired:
        log.error("Claude timed out after %ds", timeout)
        return -1, f"[TIMEOUT after {timeout}s]"
    except FileNotFoundError:
        log.error("Claude CLI not found — is it installed and in PATH?")
        return -2, "[ERROR: claude CLI not found]"
    except Exception as e:
        log.error("Claude invocation failed: %s", e)
        return -3, f"[ERROR: {e}]"
    finally:
        if prompt_file.exists():
            prompt_file.unlink()


def extract_progress_note(output: str) -> str:
    """Parse the RALPH_PROGRESS: line from Claude's output."""
    for line in output.splitlines():
        if line.strip().startswith("RALPH_PROGRESS:"):
            return line.split("RALPH_PROGRESS:", 1)[1].strip()
    # No structured line — take last non-empty line as note
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    return lines[-1][:120] if lines else "(no output)"


# ===========================================================================
# Completion Log Writer
# ===========================================================================

def write_completion_log(state: dict[str, Any], final_status: str) -> Path:
    """Write a human-readable completion log to vault/Plans/."""
    PLANS_PATH.mkdir(parents=True, exist_ok=True)
    now = _now()
    date_str = now.strftime("%Y-%m-%d")
    label = state.get("label", "task")
    filename = f"RALPH_LOG_{label}_{date_str}.md"
    path = PLANS_PATH / filename

    started = state.get("started_at", "?")
    elapsed = ""
    try:
        s = datetime.fromisoformat(started)
        sec = int((_now() - s).total_seconds())
        elapsed = f"{sec // 60}m {sec % 60}s"
    except Exception:
        elapsed = "?"

    outputs = state.get("outputs", [])
    iterations_block = ""
    for o in outputs:
        iterations_block += (
            f"\n### Iteration {o['iteration']}\n"
            f"- Note: {o.get('progress_note', '(none)')}\n"
            f"- Output chars: {o.get('chars', 0)}\n"
            f"- Exit code: {o.get('exit_code', '?')}\n"
        )

    done_check, done_msg = is_done(state)
    content = (
        f"---\n"
        f"type: ralph_loop_log\n"
        f"label: {label}\n"
        f"status: {final_status}\n"
        f"started_at: {started}\n"
        f"completed_at: {now.isoformat()}\n"
        f"elapsed: {elapsed}\n"
        f"total_iterations: {state.get('iteration', 0)}\n"
        f"max_iterations: {state.get('max_iterations', '?')}\n"
        f"done_type: {state.get('done_type', '?')}\n"
        f"completion_satisfied: {done_check}\n"
        f"---\n\n"
        f"# Ralph Loop Completion Report\n\n"
        f"**Task:** {state.get('task', '?')[:200]}\n\n"
        f"**Label:** `{label}`\n"
        f"**Status:** {final_status}\n"
        f"**Duration:** {elapsed} over {state.get('iteration', 0)} iteration(s)\n"
        f"**Completion check:** {done_msg}\n\n"
        f"## Iterations\n"
        f"{iterations_block}\n"
    )

    path.write_text(content, encoding="utf-8")
    log.info("Completion log saved: %s", filename)
    return path


# ===========================================================================
# MODE 1 — External Loop (CLI / Orchestrator)
# ===========================================================================

def run_external_loop(
    task: str,
    done_type: str,
    done_glob: str = "",
    done_path: str = "",
    done_count: int = 1,
    label: str = "",
    max_iterations: int = 15,
    batch_size: int = 4,
    timeout_per_iter: int = 300,
    force: bool = False,
) -> bool:
    """
    Run the full autonomous loop. Returns True if task completed successfully.

    This is the main entry point for orchestrator / PM2 / CLI usage.
    It calls `claude --print` in a while loop until the completion signal
    is satisfied or max_iterations is reached.
    """
    PLANS_PATH.mkdir(parents=True, exist_ok=True)

    # ── Check for existing active session ──────────────────────────────────
    existing = load_state()
    if existing.get("status") == "running" and not force:
        log.warning(
            "A ralph_loop session is already running: label=%s, iter=%d/%d",
            existing.get("label"), existing.get("iteration", 0),
            existing.get("max_iterations", 0),
        )
        print(
            f"\n⚠️  A loop is already running: {existing.get('label')}\n"
            f"   Iteration {existing.get('iteration', 0)}/{existing.get('max_iterations', 0)}\n"
            f"   Use --force to override, or --abort to stop it.\n"
        )
        return False

    # ── Build initial state ─────────────────────────────────────────────────
    now = _now()
    session_id = uuid.uuid4().hex[:8]
    label = label or f"task_{now.strftime('%Y%m%d_%H%M%S')}"

    state: dict[str, Any] = {
        "session_id":    session_id,
        "label":         label,
        "task":          task,
        "iteration":     0,
        "max_iterations": max_iterations,
        "batch_size":    batch_size,
        "started_at":    now.isoformat(),
        "last_iter_at":  now.isoformat(),
        "status":        "running",
        "done_type":     done_type,
        "done_glob":     done_glob,
        "done_path":     done_path,
        "done_count":    done_count,
        "outputs":       [],
        "mode":          "external",
    }
    save_state(state)

    print()
    print("═" * 65)
    print("  Ralph Wiggum Loop — Autonomous Task Runner")
    print("═" * 65)
    print(f"  Label   : {label}")
    print(f"  Session : {session_id}")
    print(f"  MaxIter : {max_iterations}")
    print(f"  Batch   : {batch_size} items/iter")
    print(f"  Done    : {done_type} → {done_glob or done_path}")
    print(f"  Started : {now.strftime('%Y-%m-%d %H:%M:%S PKT')}")
    print("═" * 65)
    print()
    print(f"TASK: {task[:200]}")
    print()

    # ── Pre-flight: check if already done ──────────────────────────────────
    already_done, msg = is_done(state)
    if already_done:
        log.info("Completion signal already satisfied before first iteration: %s", msg)
        print(f"✅ Task already complete: {msg}")
        state["status"] = "done"
        state["iteration"] = 0
        save_state(state)
        write_completion_log(state, "done_before_start")
        return True

    # ── Main loop ───────────────────────────────────────────────────────────
    final_status = "failed"

    try:
        while state["iteration"] < max_iterations:
            state["iteration"] += 1
            state["last_iter_at"] = _now().isoformat()
            save_state(state)

            iteration = state["iteration"]
            print(f"── Iteration {iteration}/{max_iterations} ({'%.1f' % ((iteration-1)/max_iterations*100)}% complete) ─")
            log.info("Starting iteration %d/%d for label=%s", iteration, max_iterations, label)

            # Build prompt
            if iteration == 1:
                prompt = build_initial_prompt(state)
            else:
                prompt = build_continuation_prompt(state)

            # Call Claude
            exit_code, output = run_claude(prompt, timeout=timeout_per_iter)

            if exit_code not in (0, None):
                log.warning("Claude exited with code %d on iteration %d", exit_code, iteration)
                if exit_code in (-2,):   # Claude not found — fatal
                    final_status = "error_claude_not_found"
                    break

            # Record output
            progress_note = extract_progress_note(output)
            state["outputs"].append({
                "iteration":     iteration,
                "chars":         len(output),
                "exit_code":     exit_code,
                "progress_note": progress_note,
                "timestamp":     _now().isoformat(),
            })
            save_state(state)

            print(f"   Claude output: {len(output)} chars")
            print(f"   Progress: {progress_note}")

            # ── Check completion ─────────────────────────────────────────
            done, done_msg = is_done(state)
            print(f"   Completion check: {done_msg}")

            if done:
                log.info("Completion signal satisfied after iteration %d: %s", iteration, done_msg)
                print(f"\n✅ DONE after {iteration} iteration(s): {done_msg}")
                final_status = "done"
                break

            # ── Not done — brief pause before next iteration ─────────────
            if state["iteration"] < max_iterations:
                print(f"   Waiting 3s before next iteration...")
                time.sleep(3)

        else:
            # Exhausted max iterations
            log.warning("Max iterations (%d) reached for label=%s", max_iterations, label)
            final_status = "max_iterations_reached"
            print(f"\n⚠️  Max iterations ({max_iterations}) reached without completion.")

    except KeyboardInterrupt:
        log.info("Loop aborted by user (KeyboardInterrupt)")
        final_status = "aborted"
        print("\n\n⚠️  Loop aborted by user.")

    # ── Finalize ────────────────────────────────────────────────────────────
    state["status"] = final_status
    save_state(state)
    log_path = write_completion_log(state, final_status)

    print()
    print("═" * 65)
    print(f"  Loop ended: {final_status.upper()}")
    print(f"  Iterations: {state.get('iteration', 0)}/{max_iterations}")
    print(f"  Log saved:  {log_path.name}")
    print("═" * 65)
    print()

    # Clear state if cleanly done
    if final_status == "done":
        clear_state()

    return final_status == "done"


# ===========================================================================
# MODE 2 — Stop Hook (Claude Code interactive sessions)
# ===========================================================================

def hook_check() -> None:
    """
    Called by Claude Code's Stop hook after EVERY Claude response.

    Checks whether an active ralph_loop task is in progress.
    - If active AND not done: print continuation context, exit(2)
      → exit code 2 tells Claude Code "do NOT stop, keep working"
      → the printed text becomes Claude's next context/instruction
    - If active AND done: mark complete, exit(0) → Claude stops
    - If no active session: exit(0) → Claude stops normally (no effect)
    """
    state = load_state()

    # No active session — let Claude stop normally
    if not state or state.get("status") not in ("running",):
        sys.exit(0)

    label    = state.get("label", "task")
    done_type = state.get("done_type", "signal_file")

    # Increment hook iteration counter (distinct from external loop iterations)
    hook_iter = state.get("hook_iteration", 0) + 1
    state["hook_iteration"] = hook_iter
    state["last_iter_at"] = _now().isoformat()

    max_iter = state.get("max_iterations", 15)
    if hook_iter > max_iter:
        # Safety valve — stop even if signal not satisfied
        state["status"] = "max_iterations_reached"
        save_state(state)
        write_completion_log(state, "max_iterations_reached")
        print(f"[ralph_loop] ⚠️  Max iterations ({max_iter}) reached for '{label}'. Stopping.")
        sys.exit(0)

    # Check completion
    done, done_msg = is_done(state)

    if done:
        state["status"] = "done"
        save_state(state)
        write_completion_log(state, "done")
        clear_state()
        # Print completion notice (shown to user, does NOT re-trigger Claude)
        print(f"[ralph_loop] ✅ Task '{label}' complete: {done_msg}")
        print(f"[ralph_loop] Loop log saved to vault/Plans/RALPH_LOG_{label}_*.md")
        sys.exit(0)  # Allow Claude to stop

    # ── Not done: inject continuation context and signal Claude to keep going
    ctx = count_remaining(state)
    remaining = ctx.get("pending", "?")
    handled   = ctx.get("handled", 0)
    pending_names = ctx.get("pending_names", [])

    save_state(state)

    # This output is fed back to Claude as new context
    print(
        f"[ralph_loop] CONTINUE — iteration {hook_iter}/{max_iter} for '{label}'\n"
        f"\n"
        f"The autonomous task is NOT yet complete.\n"
        f"\n"
        f"COMPLETION CHECK: {done_msg}\n"
        f"\n"
        f"PROGRESS:\n"
        f"  - Items handled so far:  {handled}\n"
        f"  - Items still pending:   {remaining}\n"
    )
    if pending_names:
        print("  Pending items:")
        for name in pending_names:
            print(f"    - {name}")
    print(
        f"\n"
        f"INSTRUCTIONS: Continue processing the next batch of "
        f"{state.get('batch_size', 4)} pending items.\n"
        f"Remember: {_done_instruction(state)}\n"
        f"\n"
        f"Process the next batch NOW.\n"
    )

    # Exit code 2 = Claude Code will NOT stop; it feeds our stdout back to Claude
    sys.exit(2)


# ===========================================================================
# STATUS / ABORT / LIST
# ===========================================================================

def show_status() -> None:
    """Print current loop status to console."""
    state = load_state()
    if not state:
        print("No active ralph_loop session.")
        return

    print()
    print("═" * 55)
    print("  Ralph Loop — Current Session")
    print("═" * 55)
    print(f"  Label    : {state.get('label')}")
    print(f"  Session  : {state.get('session_id')}")
    print(f"  Status   : {state.get('status')}")
    print(f"  Mode     : {state.get('mode', 'external')}")
    print(f"  Iteration: {state.get('iteration', 0)}/{state.get('max_iterations')}")
    print(f"  Started  : {state.get('started_at', '?')[:19]}")
    print(f"  Last iter: {state.get('last_iter_at', '?')[:19]}")
    done_check, done_msg = is_done(state)
    print(f"  Done?    : {'✅ YES' if done_check else '❌ NO'} — {done_msg}")
    print()
    print(f"  Task: {state.get('task', '?')[:100]}")
    print("═" * 55)


def abort_loop() -> None:
    """Mark current loop as aborted and clear state."""
    state = load_state()
    if not state:
        print("No active ralph_loop session to abort.")
        return

    label = state.get("label", "?")
    state["status"] = "aborted"
    save_state(state)
    write_completion_log(state, "aborted")
    clear_state()
    print(f"✅ Loop '{label}' aborted. State cleared.")


def init_hook_session(
    task: str,
    done_type: str,
    done_glob: str = "",
    done_path: str = "",
    done_count: int = 1,
    label: str = "",
    max_iterations: int = 15,
    batch_size: int = 4,
) -> None:
    """
    Initialize state for a Stop Hook session.
    Call this BEFORE starting a Claude Code interactive session that
    should use the hook-based loop.

    Example:
        python ralph_loop.py --init-hook \\
          --task "Process all Needs_Action..." \\
          --done-type signal_file \\
          --done-glob "vault/Plans/NEEDS_ACTION_COMPLETE_*.md" \\
          --label needs-action-audit

    Then start your Claude Code session normally. The Stop hook will
    automatically check after each response and keep going until done.
    """
    now = _now()
    session_id = uuid.uuid4().hex[:8]
    label = label or f"hook_{now.strftime('%Y%m%d_%H%M%S')}"

    state: dict[str, Any] = {
        "session_id":    session_id,
        "label":         label,
        "task":          task,
        "iteration":     0,
        "hook_iteration": 0,
        "max_iterations": max_iterations,
        "batch_size":    batch_size,
        "started_at":    now.isoformat(),
        "last_iter_at":  now.isoformat(),
        "status":        "running",
        "done_type":     done_type,
        "done_glob":     done_glob,
        "done_path":     done_path,
        "done_count":    done_count,
        "outputs":       [],
        "mode":          "hook",
    }
    save_state(state)

    print(f"✅ Hook session initialized: {label} ({session_id})")
    print(f"   Done type: {done_type} → {done_glob or done_path}")
    print(f"   Max iterations: {max_iterations}")
    print()
    print("Now start your Claude Code session and give Claude the task.")
    print("The Stop hook will automatically keep it running until completion.")
    print()
    print(f"Initial task prompt to paste:")
    print("─" * 60)
    print(build_initial_prompt(state))
    print("─" * 60)


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ralph Wiggum Loop — Autonomous multi-step task runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES:

  # Process all Needs_Action cards autonomously (External Loop mode)
  python ralph_loop.py \\
    --task "Read every file in vault/Needs_Action/. For each card:
            1. Classify it: actionable / informational / spam.
            2. Informational/spam: update status to archived.
            3. Actionable: update status to in_progress, create
               vault/Plans/PLAN_<cardname>.md with recommended actions.
            4. When ALL cards are handled, write the signal file." \\
    --done-type signal_file \\
    --done-glob "vault/Plans/NEEDS_ACTION_COMPLETE_*.md" \\
    --label needs-action-audit

  # Full CEO audit (runs ceo_briefing.py via Claude)
  python ralph_loop.py \\
    --task "Run the full CEO audit: call python watchers/ceo_briefing.py
            then verify the output file exists and is complete." \\
    --done-type signal_file \\
    --done-glob "vault/Plans/CEO_BRIEFING_*.md" \\
    --label ceo-audit \\
    --max-iter 3

  # Hook-based session (interactive Claude Code)
  python ralph_loop.py --init-hook \\
    --task "Process all Needs_Action..." \\
    --done-type all_handled \\
    --done-path "vault/Needs_Action" \\
    --label interactive-audit

  # Check status of running loop
  python ralph_loop.py --status

  # Abort a stuck loop
  python ralph_loop.py --abort
        """
    )

    # Mode flags (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--hook-check",  action="store_true",
                            help="Stop hook mode: check if task done, exit 2 if not")
    mode_group.add_argument("--init-hook",   action="store_true",
                            help="Initialize state for a hook-based session")
    mode_group.add_argument("--status",      action="store_true",
                            help="Show current loop status")
    mode_group.add_argument("--abort",       action="store_true",
                            help="Abort the current loop")

    # Task definition
    parser.add_argument("--task",       default="", help="Full task description for Claude")
    parser.add_argument("--label",      default="", help="Short label for this task (used in filenames)")
    parser.add_argument("--max-iter",   type=int, default=15, help="Max iterations (default: 15)")
    parser.add_argument("--batch",      type=int, default=4,  help="Items per iteration (default: 4)")
    parser.add_argument("--timeout",    type=int, default=300, help="Seconds per Claude call (default: 300)")
    parser.add_argument("--force",      action="store_true",
                        help="Override existing session")

    # Completion signal
    parser.add_argument("--done-type",  default="signal_file",
                        choices=["signal_file", "empty_dir", "all_handled", "file_count"],
                        help="Completion detection method")
    parser.add_argument("--done-glob",  default="",
                        help="Glob pattern for signal_file (e.g. vault/Plans/DONE_*.md)")
    parser.add_argument("--done-path",  default="",
                        help="Directory path for empty_dir/all_handled/file_count")
    parser.add_argument("--done-count", type=int, default=1,
                        help="Required file count for file_count mode")

    args = parser.parse_args()

    # ── Dispatch ────────────────────────────────────────────────────────────
    if args.hook_check:
        hook_check()

    elif args.status:
        show_status()

    elif args.abort:
        abort_loop()

    elif args.init_hook:
        if not args.task:
            parser.error("--init-hook requires --task")
        init_hook_session(
            task=args.task,
            done_type=args.done_type,
            done_glob=args.done_glob,
            done_path=args.done_path,
            done_count=args.done_count,
            label=args.label,
            max_iterations=args.max_iter,
            batch_size=args.batch,
        )

    else:
        # External loop mode (default)
        if not args.task:
            parser.error("--task is required to start a loop")

        # Validate completion args
        if args.done_type == "signal_file" and not args.done_glob:
            parser.error("--done-type signal_file requires --done-glob")
        if args.done_type in ("empty_dir", "all_handled", "file_count") and not args.done_path:
            parser.error(f"--done-type {args.done_type} requires --done-path")

        success = run_external_loop(
            task=args.task,
            done_type=args.done_type,
            done_glob=args.done_glob,
            done_path=args.done_path,
            done_count=args.done_count,
            label=args.label,
            max_iterations=args.max_iter,
            batch_size=args.batch,
            timeout_per_iter=args.timeout,
            force=args.force,
        )
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
