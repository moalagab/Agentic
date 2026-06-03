#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Smart Field — VPS Setup Script
# Ubuntu 24.04 LTS | Python 3.12 | Caddy | systemd
#
# تشغيل: bash setup_server.sh <your-domain-or-ip>
# مثال:  bash setup_server.sh smartfield.yourdomain.com
#        bash setup_server.sh 1.2.3.4.sslip.io
# ═══════════════════════════════════════════════════════════

set -euo pipefail

DOMAIN="${1:-}"
APP_DIR="/opt/smartfield"
APP_USER="smartfield"

if [ -z "$DOMAIN" ]; then
  echo "Usage: bash setup_server.sh <domain>"
  echo "Example: bash setup_server.sh api.smartfield.sa"
  exit 1
fi

echo "════════════════════════════════════════"
echo " Smart Field VPS Setup"
echo " Domain: $DOMAIN"
echo " App dir: $APP_DIR"
echo "════════════════════════════════════════"

# ── 1. System update ─────────────────────────────────────────
echo "[1/8] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

# ── 2. Install dependencies ──────────────────────────────────
echo "[2/8] Installing system packages..."
apt-get install -y -qq \
  python3.12 python3.12-venv python3.12-dev python3-pip \
  git curl wget unzip \
  libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf2.0-0 \
  libffi-dev libssl-dev \
  fonts-dejavu fonts-liberation \
  ufw

# ── 3. Install Caddy ─────────────────────────────────────────
echo "[3/8] Installing Caddy (auto-HTTPS)..."
apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq && apt-get install caddy -y -qq

# ── 4. Create app user ───────────────────────────────────────
echo "[4/8] Creating app user..."
id -u "$APP_USER" &>/dev/null || useradd -r -s /bin/false -d "$APP_DIR" "$APP_USER"
mkdir -p "$APP_DIR" "$APP_DIR/data/proposals" "$APP_DIR/logs"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# ── 5. Python virtualenv ─────────────────────────────────────
echo "[5/8] Setting up Python environment..."
python3.12 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip -q

# ── 6. Firewall ──────────────────────────────────────────────
echo "[6/8] Configuring firewall..."
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# ── 7. Caddyfile ─────────────────────────────────────────────
echo "[7/8] Configuring Caddy..."
cat > /etc/caddy/Caddyfile << CADDY
$DOMAIN {
    reverse_proxy localhost:8000
    encode gzip
    log {
        output file /var/log/caddy/smartfield.log
        format json
    }
}
CADDY

mkdir -p /var/log/caddy
chown caddy:caddy /var/log/caddy
systemctl enable caddy
systemctl restart caddy

# ── 8. systemd service ───────────────────────────────────────
echo "[8/8] Creating systemd service..."
cat > /etc/systemd/system/smartfield.service << SERVICE
[Unit]
Description=Smart Field Lead Generation Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/uvicorn channels.webhook_server:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
StandardOutput=append:$APP_DIR/logs/app.log
StandardError=append:$APP_DIR/logs/app.log
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable smartfield

echo ""
echo "════════════════════════════════════════"
echo " Setup complete!"
echo " Next: upload your code and .env, then:"
echo "   systemctl start smartfield"
echo "   systemctl status smartfield"
echo ""
echo " Your webhook URL will be:"
echo "   https://$DOMAIN/webhook/telegram"
echo "   https://$DOMAIN/webhook/whatsapp"
echo "   https://$DOMAIN/dashboard"
echo "════════════════════════════════════════"
