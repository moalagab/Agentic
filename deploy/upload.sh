#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Smart Field — Upload code to VPS
# شغّله من جهازك (Windows Git Bash)
#
# الاستخدام: bash deploy/upload.sh root@YOUR_VPS_IP
# مثال:     bash deploy/upload.sh root@5.75.180.200
# ═══════════════════════════════════════════════════════════

set -euo pipefail

SERVER="${1:-}"
APP_DIR="/opt/smartfield"

if [ -z "$SERVER" ]; then
  echo "Usage: bash deploy/upload.sh root@YOUR_SERVER_IP"
  exit 1
fi

echo "Uploading Smart Field to $SERVER ..."

# Sync all code (exclude unnecessary files)
rsync -avz --progress \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='data/proposals/*' \
  --exclude='*.db' \
  --exclude='deploy/upload.sh' \
  . "$SERVER:$APP_DIR/"

# Upload .env separately (sensitive)
echo "Uploading .env ..."
scp .env "$SERVER:$APP_DIR/.env"
ssh "$SERVER" "chmod 600 $APP_DIR/.env && chown smartfield:smartfield $APP_DIR/.env"

# Install Python dependencies on server
echo "Installing dependencies on server..."
ssh "$SERVER" "
  cd $APP_DIR && \
  $APP_DIR/.venv/bin/pip install -r requirements.txt -q && \
  chown -R smartfield:smartfield $APP_DIR
"

echo ""
echo "Upload complete!"
echo "To start the service: ssh $SERVER 'systemctl restart smartfield'"
echo "To check logs:        ssh $SERVER 'journalctl -u smartfield -f'"
