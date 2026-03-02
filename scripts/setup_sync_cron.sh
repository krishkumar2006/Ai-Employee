#!/usr/bin/env bash
# =============================================================================
# setup_sync_cron.sh — Platinum Tier
# =============================================================================
# Installs vault_sync.sh as a cron job on the Oracle VM (Ubuntu).
#
# Run ONCE on the VM after deploying the project:
#   bash scripts/setup_sync_cron.sh
#
# What it adds to crontab:
#   • vault_sync every 5 minutes
#   • claim_agent (orchestrator) at startup
#   • PM2 resurrect safety net every minute
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
SYNC_LOG="$REPO_ROOT/vault/Logs/sync.log"
CLAIM_LOG="$REPO_ROOT/vault/Logs/claim_agent.log"

echo "Setting up cron jobs for vault sync..."
echo "Repo: $REPO_ROOT"

# ---------------------------------------------------------------------------
# Make scripts executable
# ---------------------------------------------------------------------------
chmod +x "$SCRIPT_DIR/vault_sync.sh"

# ---------------------------------------------------------------------------
# Build cron lines
# ---------------------------------------------------------------------------
CRON_SYNC="*/5 * * * * /bin/bash $SCRIPT_DIR/vault_sync.sh >> $SYNC_LOG 2>&1"
CRON_CLAIM="@reboot $VENV_PYTHON $SCRIPT_DIR/claim_agent.py --agent orchestrator >> $CLAIM_LOG 2>&1"
CRON_PM2="* * * * * pm2 resurrect > /dev/null 2>&1 || true"
CRON_PING="*/10 * * * * ping -c 1 8.8.8.8 > /dev/null 2>&1"

# ---------------------------------------------------------------------------
# Install cron entries (idempotent — skip if already present)
# ---------------------------------------------------------------------------
install_cron() {
    local entry="$1"
    local label="$2"
    # Check if already installed
    if crontab -l 2>/dev/null | grep -qF "$entry"; then
        echo "  [skip] Already installed: $label"
    else
        # Append to existing crontab
        ( crontab -l 2>/dev/null; echo "$entry" ) | crontab -
        echo "  [+] Installed: $label"
    fi
}

install_cron "$CRON_SYNC"  "vault_sync every 5 min"
install_cron "$CRON_CLAIM" "claim_agent on reboot"
install_cron "$CRON_PM2"   "pm2 resurrect every 1 min"
install_cron "$CRON_PING"  "anti-idle ping every 10 min"

echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "Done. vault_sync.sh will run every 5 minutes."
echo "Sync log: $SYNC_LOG"
