#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Smart Field — Register webhook URLs after deployment
# شغّله بعد ما السيرفر يشتغل
#
# الاستخدام: bash deploy/update_webhooks.sh https://your-domain.com
# ═══════════════════════════════════════════════════════════

BASE_URL="${1:-}"

if [ -z "$BASE_URL" ]; then
  echo "Usage: bash deploy/update_webhooks.sh https://your-domain.com"
  exit 1
fi

# Load .env
source .env 2>/dev/null || true

echo "Registering webhooks for $BASE_URL ..."

# ── Telegram webhook ─────────────────────────────────────────
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo -n "Telegram webhook ... "
  RESULT=$(curl -s -X POST \
    "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
    -d "url=$BASE_URL/webhook/telegram")
  echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d.get('ok') else d.get('description','FAIL'))"
else
  echo "Telegram: TELEGRAM_BOT_TOKEN not set — skip"
fi

echo ""
echo "════════════════════════════════════════"
echo " Webhook URLs registered:"
echo "   Telegram:    $BASE_URL/webhook/telegram"
echo "   WhatsApp:    $BASE_URL/webhook/whatsapp"
echo "   LinkedIn:    $BASE_URL/webhook/linkedin"
echo "   Google Forms:$BASE_URL/webhook/google-forms"
echo "   Website:     $BASE_URL/webhook/website"
echo "   Dashboard:   $BASE_URL/dashboard"
echo "   API:         $BASE_URL/api/lead"
echo "════════════════════════════════════════"
