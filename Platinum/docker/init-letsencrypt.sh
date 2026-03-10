#!/usr/bin/env bash
# =============================================================================
# init-letsencrypt.sh — Oracle VM
# =============================================================================
# Run ONCE on a fresh VM to get your first Let's Encrypt certificate.
# After this, certbot in docker-compose handles auto-renewal every 12 hours.
#
# Prerequisites:
#   1. Your domain DNS A-record must already point to this VM's public IP
#   2. Oracle Security List must have port 80 + 443 open (TCP ingress)
#   3. OS firewall must allow port 80 + 443 (run: sudo ufw allow 80 443/tcp)
#   4. docker/.env.docker must be filled in (DOMAIN, CERTBOT_EMAIL)
#   5. docker compose must NOT be running yet (or nginx must be stopped)
#
# Usage:
#   cd ~/ai-employee/docker
#   chmod +x init-letsencrypt.sh
#   bash init-letsencrypt.sh
# =============================================================================

set -euo pipefail

# Load .env.docker
ENV_FILE="$(dirname "$0")/.env.docker"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found."
    echo "       Copy docker/.env.docker.example to docker/.env.docker and fill in values."
    exit 1
fi

# Parse .env.docker (simple key=value)
export $(grep -v '^#' "$ENV_FILE" | grep -v '^$' | xargs)

echo "==========================================="
echo "  Let's Encrypt Initial Certificate Setup"
echo "==========================================="
echo "  Domain : $DOMAIN"
echo "  Email  : $CERTBOT_EMAIL"
echo ""

# Verify domain resolves to this machine
MY_IP=$(curl -sf https://api.ipify.org 2>/dev/null || echo "unknown")
echo "  VM public IP: $MY_IP"
echo "  Make sure $DOMAIN has an A-record pointing to $MY_IP"
echo ""
read -p "  Domain DNS configured correctly? [y/N]: " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Step 1: Create volume directories
# ---------------------------------------------------------------------------
echo ""
echo "[1/5] Creating certbot volume directories..."
mkdir -p ./nginx/certbot/www
mkdir -p ./addons

# ---------------------------------------------------------------------------
# Step 2: Replace YOURDOMAIN.COM in nginx.conf with actual domain
# ---------------------------------------------------------------------------
echo "[2/5] Updating nginx.conf with domain: $DOMAIN ..."
if grep -q "YOURDOMAIN.COM" ./nginx/nginx.conf; then
    sed -i "s/YOURDOMAIN.COM/$DOMAIN/g" ./nginx/nginx.conf
    echo "      nginx.conf updated."
else
    echo "      nginx.conf already has domain set (skipping)."
fi

# ---------------------------------------------------------------------------
# Step 3: Start nginx with HTTP-only config (for certbot challenge)
# ---------------------------------------------------------------------------
echo "[3/5] Starting nginx (HTTP only) for challenge..."

# Create a temporary HTTP-only nginx config for the challenge
cat > ./nginx/nginx-http-only.conf << NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 200 'OK';
        add_header Content-Type text/plain;
    }
}
NGINXEOF

docker run -d --rm \
    --name nginx-certbot-init \
    -p 80:80 \
    -v "$SCRIPT_DIR/nginx/nginx-http-only.conf:/etc/nginx/conf.d/default.conf:ro" \
    -v certbot_www_init:/var/www/certbot \
    nginx:alpine

echo "      Nginx started for HTTP challenge."

# ---------------------------------------------------------------------------
# Step 4: Get certificate
# ---------------------------------------------------------------------------
echo "[4/5] Requesting Let's Encrypt certificate for $DOMAIN ..."
sleep 3  # Give nginx a moment to start

docker run --rm \
    -v certbot_certs_init:/etc/letsencrypt \
    -v certbot_www_init:/var/www/certbot \
    certbot/certbot certonly \
        --webroot \
        --webroot-path=/var/www/certbot \
        --email "$CERTBOT_EMAIL" \
        --agree-tos \
        --no-eff-email \
        -d "$DOMAIN" \
        --non-interactive

echo "      Certificate obtained."

# Stop temporary nginx
docker stop nginx-certbot-init 2>/dev/null || true

# Copy certs from init volumes to named volumes used by docker-compose
echo "      Syncing cert volumes..."
docker run --rm \
    -v certbot_certs_init:/src:ro \
    -v ai-employee-odoo_certbot_certs:/dst \
    alpine sh -c "cp -a /src/. /dst/"

docker run --rm \
    -v certbot_www_init:/src:ro \
    -v ai-employee-odoo_certbot_www:/dst \
    alpine sh -c "cp -a /src/. /dst/"

# Clean up init volumes
docker volume rm certbot_certs_init certbot_www_init 2>/dev/null || true
rm -f ./nginx/nginx-http-only.conf

# ---------------------------------------------------------------------------
# Step 5: Start full docker compose stack
# ---------------------------------------------------------------------------
echo "[5/5] Starting full docker compose stack..."
docker compose --env-file .env.docker up -d

echo ""
echo "==========================================="
echo "  Setup complete!"
echo "==========================================="
echo ""
echo "  Odoo URL   : https://$DOMAIN"
echo "  Admin login: $ODOO_USER"
echo "  Admin pass : (set in .env.docker)"
echo ""
echo "  Commands:"
echo "    docker compose logs -f odoo     # tail Odoo logs"
echo "    docker compose ps               # container status"
echo "    docker compose restart odoo     # restart Odoo"
echo ""
echo "  Next steps:"
echo "    1. Open https://$DOMAIN"
echo "    2. Log in and complete the setup wizard"
echo "    3. Go to Settings > Activate Developer Mode"
echo "    4. Configure email in Settings > Technical > Outgoing Mail"
echo ""
