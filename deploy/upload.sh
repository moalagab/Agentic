#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Smart Field — Deploy to Hetzner VPS
# Ubuntu 26.04 | Python 3.14 | nginx | systemd
#
# الاستخدام: bash deploy/upload.sh [user@IP]
# الافتراضي: root@178.105.185.3
# ═══════════════════════════════════════════════════════════

set -euo pipefail

SERVER="${1:-root@178.105.185.3}"
APP_DIR="/home/smartfield/app"
APP_USER="smartfield"
VENV="$APP_DIR/.venv"
SERVICE="smartfield"

echo "════════════════════════════════════════"
echo " Smart Field — Deploy"
echo " Server : $SERVER"
echo " App dir: $APP_DIR"
echo "════════════════════════════════════════"

# ── 1. Sync code ──────────────────────────────────────────────
echo ""
echo "[1/4] Syncing code..."
rsync -az --progress \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='.env' \
  --exclude='server.log' \
  --exclude='data/proposals/*' \
  --exclude='dashboard_test.html' \
  . "$SERVER:$APP_DIR/"

# ── 2. Upload .env ────────────────────────────────────────────
echo ""
echo "[2/4] Uploading .env ..."
scp .env "$SERVER:$APP_DIR/.env"
ssh "$SERVER" "chmod 600 $APP_DIR/.env && chown $APP_USER:$APP_USER $APP_DIR/.env"

# ── 3. Install dependencies ───────────────────────────────────
echo ""
echo "[3/4] Installing Python dependencies..."
ssh "$SERVER" "
  set -e
  # Create venv with Python 3.14 if it doesn't exist
  if [ ! -f '$VENV/bin/python' ]; then
    echo 'Creating virtualenv...'
    python3.14 -m venv $VENV
  fi
  $VENV/bin/pip install --upgrade pip -q
  $VENV/bin/pip install -r $APP_DIR/requirements.txt -q
  chown -R $APP_USER:$APP_USER $APP_DIR
  echo 'Dependencies installed.'
"

# ── 4. Restart service ────────────────────────────────────────
echo ""
echo "[4/4] Restarting smartfield service..."
ssh "$SERVER" "
  systemctl daemon-reload
  systemctl restart $SERVICE
  sleep 3
  systemctl is-active $SERVICE && echo 'Service: RUNNING' || echo 'Service: FAILED'
"

echo ""
echo "════════════════════════════════════════"
echo " Deploy complete!"
echo ""
echo " Health check:"
curl -sf "https://agent.smartfield.sa/health" 2>/dev/null \
  && echo " https://agent.smartfield.sa/health — OK" \
  || echo " (domain may need a moment to propagate)"
echo ""
echo " Commands:"
echo "   Logs:    ssh $SERVER 'journalctl -u $SERVICE -f'"
echo "   Status:  ssh $SERVER 'systemctl status $SERVICE'"
echo "   Dashboard: https://agent.smartfield.sa/dashboard"
echo "════════════════════════════════════════"
