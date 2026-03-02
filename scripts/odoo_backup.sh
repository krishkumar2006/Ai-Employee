#!/usr/bin/env bash
# =============================================================================
# odoo_backup.sh — Daily Odoo backup (PostgreSQL + filestore)
# =============================================================================
# Runs daily at 02:00 PKT (21:00 UTC) via cron.
# Keeps 7 days of backups. No paid services — all local + rsync-based.
#
# Backups stored at:
#   ~/odoo-backups/
#     YYYY-MM-DD_HH-MM/
#       odoo_db.sql.gz          (PostgreSQL dump)
#       odoo_filestore.tar.gz   (Odoo attachments/filestore)
#       backup.manifest         (metadata)
#
# Optional off-site sync (uncomment rsync section below):
#   Syncs to a second Oracle VM, NAS, or any SSH-accessible server.
#
# Install: bash scripts/setup_odoo_cron.sh
# Manual:  bash scripts/odoo_backup.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_BASE="$HOME/odoo-backups"
DOCKER_DIR="$REPO_ROOT/docker"
LOG_FILE="$REPO_ROOT/vault/Logs/odoo_backup.log"
KEEP_DAYS=7

# Compose project name (matches `name:` in docker-compose.yaml)
COMPOSE_PROJECT="ai-employee-odoo"
POSTGRES_CONTAINER="${COMPOSE_PROJECT}-postgres-1"
ODOO_CONTAINER="${COMPOSE_PROJECT}-odoo-1"

PKT_TS=$(TZ="Asia/Karachi" date "+%Y-%m-%d_%H-%M")
BACKUP_DIR="$BACKUP_BASE/$PKT_TS"
LOG_PREFIX="[odoo_backup $PKT_TS]"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
mkdir -p "$REPO_ROOT/vault/Logs"
log() { echo "$LOG_PREFIX $*" | tee -a "$LOG_FILE"; }
log_err() { echo "$LOG_PREFIX ERROR: $*" | tee -a "$LOG_FILE" >&2; }

log "Starting backup..."

# ---------------------------------------------------------------------------
# Verify containers are running
# ---------------------------------------------------------------------------
if ! docker ps --format '{{.Names}}' | grep -q "$POSTGRES_CONTAINER"; then
    log_err "PostgreSQL container '$POSTGRES_CONTAINER' is not running."
    log_err "Run: docker compose -f $DOCKER_DIR/docker-compose.yaml --env-file $DOCKER_DIR/.env.docker up -d"
    exit 1
fi

mkdir -p "$BACKUP_DIR"
log "Backup directory: $BACKUP_DIR"

# ---------------------------------------------------------------------------
# 1. PostgreSQL dump (inside container, piped out via docker exec)
# ---------------------------------------------------------------------------
log "Dumping PostgreSQL database 'odoo'..."
DB_FILE="$BACKUP_DIR/odoo_db.sql.gz"

docker exec "$POSTGRES_CONTAINER" \
    pg_dump -U odoo -d odoo --no-password 2>/dev/null \
    | gzip > "$DB_FILE"

DB_SIZE=$(du -sh "$DB_FILE" | cut -f1)
log "Database dump: $DB_FILE ($DB_SIZE)"

# ---------------------------------------------------------------------------
# 2. Odoo filestore (attachments, documents, etc.)
# ---------------------------------------------------------------------------
log "Backing up Odoo filestore..."
FS_FILE="$BACKUP_DIR/odoo_filestore.tar.gz"

# Mount the odoo_data named volume and tar it out
docker run --rm \
    --volumes-from "$ODOO_CONTAINER" \
    -v "$BACKUP_DIR:/backup" \
    alpine:latest \
    tar czf /backup/odoo_filestore.tar.gz \
        -C /var/lib/odoo \
        --exclude="./odoo-server.log" \
        --exclude="./sessions" \
        . 2>/dev/null

FS_SIZE=$(du -sh "$FS_FILE" | cut -f1)
log "Filestore backup: $FS_FILE ($FS_SIZE)"

# ---------------------------------------------------------------------------
# 3. Write backup manifest
# ---------------------------------------------------------------------------
MANIFEST="$BACKUP_DIR/backup.manifest"
cat > "$MANIFEST" << EOF
{
  "timestamp_pkt": "$PKT_TS",
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "postgres_container": "$POSTGRES_CONTAINER",
  "odoo_container": "$ODOO_CONTAINER",
  "files": {
    "database": "odoo_db.sql.gz",
    "filestore": "odoo_filestore.tar.gz"
  },
  "sizes": {
    "database": "$DB_SIZE",
    "filestore": "$FS_SIZE"
  },
  "status": "ok"
}
EOF

log "Manifest written: $MANIFEST"

# ---------------------------------------------------------------------------
# 4. Cleanup old backups (keep last KEEP_DAYS days)
# ---------------------------------------------------------------------------
log "Cleaning up backups older than $KEEP_DAYS days..."
DELETED=0
while IFS= read -r old_dir; do
    rm -rf "$old_dir"
    DELETED=$((DELETED + 1))
    log "  Deleted: $old_dir"
done < <(find "$BACKUP_BASE" -maxdepth 1 -mindepth 1 -type d -mtime "+$KEEP_DAYS" 2>/dev/null)

log "Cleaned up $DELETED old backup(s)."

# ---------------------------------------------------------------------------
# 5. Report summary
# ---------------------------------------------------------------------------
TOTAL_BACKUPS=$(ls -1 "$BACKUP_BASE" | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_BASE" 2>/dev/null | cut -f1)
log "Backup complete. Total: $TOTAL_BACKUPS backups, $TOTAL_SIZE used."

# ---------------------------------------------------------------------------
# 6. Optional: rsync to off-site SSH server (uncomment to enable)
# ---------------------------------------------------------------------------
# REMOTE_HOST="backup-server.example.com"
# REMOTE_DIR="/backups/odoo"
# REMOTE_USER="ubuntu"
# REMOTE_KEY="$HOME/.ssh/backup_key"
#
# if [ -f "$REMOTE_KEY" ]; then
#     log "Syncing to remote: $REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR"
#     rsync -az --delete \
#         -e "ssh -i $REMOTE_KEY -o StrictHostKeyChecking=no" \
#         "$BACKUP_BASE/" \
#         "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"
#     log "Remote sync complete."
# fi

# ---------------------------------------------------------------------------
# 7. Write health status for odoo_health.py to consume
# ---------------------------------------------------------------------------
HEALTH_FILE="$REPO_ROOT/vault/Logs/HEALTH_ODOO_BACKUP.json"
cat > "$HEALTH_FILE" << EOF
{
  "last_backup": "$PKT_TS",
  "last_backup_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "db_size": "$DB_SIZE",
  "filestore_size": "$FS_SIZE",
  "total_backups": $TOTAL_BACKUPS,
  "total_size_on_disk": "$TOTAL_SIZE",
  "status": "ok"
}
EOF

log "Health file updated: $HEALTH_FILE"
log "=== Backup finished ==="
