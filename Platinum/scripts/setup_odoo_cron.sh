#!/usr/bin/env bash
# =============================================================================
# setup_odoo_cron.sh — Oracle VM
# =============================================================================
# Installs cron jobs for:
#   • Odoo health check every 5 minutes
#   • Odoo backup daily at 02:00 PKT (21:00 UTC)
#   • Docker restart policy check every hour (restart if unhealthy)
#
# Run once on the Oracle VM:
#   bash scripts/setup_odoo_cron.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
VAULT_LOGS="$REPO_ROOT/vault/Logs"

mkdir -p "$VAULT_LOGS"
chmod +x "$SCRIPT_DIR/odoo_backup.sh"

echo "Setting up Odoo cron jobs..."
echo "Repo: $REPO_ROOT"

# ---------------------------------------------------------------------------
# Build cron entries
# ---------------------------------------------------------------------------
CRON_HEALTH="*/5 * * * * $VENV_PYTHON $SCRIPT_DIR/odoo_health.py --quiet >> $VAULT_LOGS/odoo_health_cron.log 2>&1"
CRON_BACKUP="0 21 * * * /bin/bash $SCRIPT_DIR/odoo_backup.sh >> $VAULT_LOGS/odoo_backup_cron.log 2>&1"
CRON_WATCHDOG="*/15 * * * * cd $REPO_ROOT/docker && docker compose --env-file .env.docker up -d --no-recreate >> $VAULT_LOGS/docker_watchdog.log 2>&1"
CRON_CERT_RENEW="0 */12 * * * docker compose -f $REPO_ROOT/docker/docker-compose.yaml --env-file $REPO_ROOT/docker/.env.docker run --rm certbot certbot renew --quiet >> $VAULT_LOGS/certbot_renew.log 2>&1"

# ---------------------------------------------------------------------------
# Install cron entries (idempotent)
# ---------------------------------------------------------------------------
install_cron() {
    local entry="$1"
    local label="$2"
    if crontab -l 2>/dev/null | grep -qF "$(echo "$entry" | awk '{print $6}')"; then
        echo "  [skip] Already installed: $label"
    else
        ( crontab -l 2>/dev/null; echo "$entry" ) | crontab -
        echo "  [+] Installed: $label"
    fi
}

install_cron "$CRON_HEALTH"   "odoo_health.py every 5 min"
install_cron "$CRON_BACKUP"   "odoo_backup.sh daily at 02:00 PKT (21:00 UTC)"
install_cron "$CRON_WATCHDOG" "docker compose up --no-recreate every 15 min"
install_cron "$CRON_CERT_RENEW" "certbot renew every 12 hours"

echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "Done. Odoo health checked every 5 min. Backup runs daily at 21:00 UTC."
echo "Backup location: ~/odoo-backups/"
echo "Health log: $VAULT_LOGS/odoo_health_cron.log"
