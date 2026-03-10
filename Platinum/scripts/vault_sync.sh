#!/usr/bin/env bash
# =============================================================================
# vault_sync.sh — Platinum Tier
# =============================================================================
# Git-based vault sync: pull → stage → commit → push
#
# Rules
# -----
#   1. Secrets (.env, *.key, *.pem, credentials*, token*) NEVER committed.
#   2. Dashboard.md uses LOCAL WINS (ours merge strategy) on conflict.
#   3. vault/Logs/, vault/Queue/, vault/In_Progress/ are EXCLUDED from sync.
#   4. A lock file prevents concurrent runs.
#   5. Push failures are retried once; then the error is logged and we exit
#      cleanly (no crash — next cron run will try again).
#
# Usage
# -----
#   bash scripts/vault_sync.sh                    # normal sync
#   bash scripts/vault_sync.sh --push-only        # skip pull, just push
#   bash scripts/vault_sync.sh --pull-only        # skip commit+push
#   bash scripts/vault_sync.sh --status           # show git status and exit
#   bash scripts/vault_sync.sh --dry-run          # pull only; show what would be staged/pushed
#   DRY_RUN=true bash scripts/vault_sync.sh       # same via env var
#
# Cron (every 5 minutes):
#   */5 * * * * /bin/bash /home/ubuntu/ai-employee/scripts/vault_sync.sh >> \
#               /home/ubuntu/ai-employee/vault/Logs/sync.log 2>&1
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK_FILE="/tmp/vault_sync.lock"
LOG_PREFIX="[vault_sync $(date '+%Y-%m-%d %H:%M:%S PKT')]"
BRANCH="${VAULT_SYNC_BRANCH:-main}"
REMOTE="${VAULT_SYNC_REMOTE:-origin}"
PUSH_ONLY=false
PULL_ONLY=false
STATUS_ONLY=false
DRY_RUN="${DRY_RUN:-false}"   # honour env var OR --dry-run flag

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
for arg in "$@"; do
    case $arg in
        --push-only)  PUSH_ONLY=true  ;;
        --pull-only)  PULL_ONLY=true  ;;
        --status)     STATUS_ONLY=true ;;
        --dry-run)    DRY_RUN=true    ;;
        *) echo "$LOG_PREFIX Unknown arg: $arg" ;;
    esac
done

if [ "$DRY_RUN" = "true" ]; then
    echo "$LOG_PREFIX DRY RUN MODE — pull will proceed; commit and push are SKIPPED."
    PULL_ONLY=true   # dry-run = pull + show diff only
fi

# ---------------------------------------------------------------------------
# cd to repo
# ---------------------------------------------------------------------------
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Status-only mode
# ---------------------------------------------------------------------------
if $STATUS_ONLY; then
    echo "$LOG_PREFIX === Git Status ==="
    git status --short
    echo "$LOG_PREFIX === Pending commits ==="
    git log --oneline "$REMOTE/$BRANCH"..HEAD 2>/dev/null || echo "(no remote tracking)"
    exit 0
fi

# ---------------------------------------------------------------------------
# Lock — prevent concurrent runs
# ---------------------------------------------------------------------------
if [ -e "$LOCK_FILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(date -r "$LOCK_FILE" +%s 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -lt 300 ]; then
        echo "$LOG_PREFIX Another sync is running (lock age ${LOCK_AGE}s). Skipping."
        exit 0
    else
        echo "$LOG_PREFIX Stale lock (${LOCK_AGE}s old). Removing."
        rm -f "$LOCK_FILE"
    fi
fi

touch "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

echo "$LOG_PREFIX Starting vault sync | branch=$BRANCH remote=$REMOTE"

# ---------------------------------------------------------------------------
# Verify git repo
# ---------------------------------------------------------------------------
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "$LOG_PREFIX ERROR: Not a git repository. Run: git init && git remote add origin <url>"
    exit 1
fi

# Check remote exists
if ! git remote | grep -q "^$REMOTE$"; then
    echo "$LOG_PREFIX ERROR: Remote '$REMOTE' not configured."
    echo "       Run: git remote add $REMOTE https://github.com/YOU/ai-employee.git"
    exit 1
fi

# ---------------------------------------------------------------------------
# PULL — rebase to avoid merge commits
# ---------------------------------------------------------------------------
if ! $PUSH_ONLY; then
    echo "$LOG_PREFIX Pulling from $REMOTE/$BRANCH (rebase)..."

    # Stash any uncommitted changes so pull can proceed cleanly
    STASHED=false
    if ! git diff --quiet || ! git diff --cached --quiet; then
        git stash push -m "vault_sync_auto_stash_$(date +%s)"
        STASHED=true
        echo "$LOG_PREFIX Stashed local changes."
    fi

    # Pull with rebase
    if git pull --rebase "$REMOTE" "$BRANCH" 2>&1; then
        echo "$LOG_PREFIX Pull OK."
    else
        echo "$LOG_PREFIX Pull/rebase failed — attempting abort + restore."
        git rebase --abort 2>/dev/null || true
        if $STASHED; then
            git stash pop 2>/dev/null || true
        fi
        echo "$LOG_PREFIX Pull skipped (will retry next cycle)."
    fi

    # Pop stash — merge conflicts on Dashboard.md → ours wins
    if $STASHED; then
        if ! git stash pop 2>&1; then
            echo "$LOG_PREFIX Stash pop conflict — applying ours strategy for Dashboard.md"
            git checkout --ours vault/Dashboard.md 2>/dev/null || true
            git add vault/Dashboard.md 2>/dev/null || true
            git stash drop 2>/dev/null || true
        fi
        echo "$LOG_PREFIX Stash restored."
    fi
fi

# ---------------------------------------------------------------------------
# COMMIT — stage vault task folders only (never secrets or runtime files)
# ---------------------------------------------------------------------------
if ! $PULL_ONLY; then
    echo "$LOG_PREFIX Staging vault changes..."

    # Stage only workflow folders (never Logs, Queue, In_Progress, .env, keys)
    SYNC_PATHS=(
        "vault/Needs_Action"
        "vault/Plans"
        "vault/Pending_Approval"
        "vault/Done"
        "vault/Updates"
        "vault/Dashboard.md"
        "vault/SKILLS.md"
        "vault/Business_Goals.md"
        "vault/Company_Handbook.md"
    )

    STAGED=false
    for path in "${SYNC_PATHS[@]}"; do
        if [ -e "$path" ]; then
            git add "$path" 2>/dev/null && STAGED=true
        fi
    done

    # Check if there's anything to commit
    if git diff --cached --quiet; then
        echo "$LOG_PREFIX Nothing to commit. Vault is up to date."
    elif [ "$DRY_RUN" = "true" ]; then
        echo "$LOG_PREFIX [DRY RUN] Would commit the following staged changes:"
        git diff --cached --stat
        echo "$LOG_PREFIX [DRY RUN] Push skipped."
        git reset HEAD -- . 2>/dev/null || true  # unstage, leave files intact
    else
        COMMIT_MSG="vault-sync: auto $(date '+%Y-%m-%d %H:%M PKT')"
        git commit -m "$COMMIT_MSG" \
            --author="VaultSync Bot <vault-sync@ai-employee.local>"
        echo "$LOG_PREFIX Committed: $COMMIT_MSG"
    fi

    # ---------------------------------------------------------------------------
    # PUSH
    # ---------------------------------------------------------------------------
    echo "$LOG_PREFIX Pushing to $REMOTE/$BRANCH..."

    PUSH_OK=false
    if git push "$REMOTE" "$BRANCH" 2>&1; then
        PUSH_OK=true
        echo "$LOG_PREFIX Push OK."
    else
        echo "$LOG_PREFIX Push failed. Retrying in 5s..."
        sleep 5
        if git push "$REMOTE" "$BRANCH" 2>&1; then
            PUSH_OK=true
            echo "$LOG_PREFIX Push OK (retry)."
        else
            echo "$LOG_PREFIX Push failed after retry. Will retry next cycle."
            # Write failure marker so health_check.sh can alert
            echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"error\":\"push_failed\"}" \
                > vault/Logs/sync_error.json 2>/dev/null || true
        fi
    fi

    if $PUSH_OK; then
        # Clear any previous error marker
        rm -f vault/Logs/sync_error.json 2>/dev/null || true
    fi
fi

echo "$LOG_PREFIX Sync complete."
