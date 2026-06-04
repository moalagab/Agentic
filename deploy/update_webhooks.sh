#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Smart Field — Register webhook URLs after deployment
# الاستخدام: bash deploy/update_webhooks.sh [base_url]
# الافتراضي: https://agent.smartfield.sa
# ═══════════════════════════════════════════════════════════

BASE_URL="${1:-https://agent.smartfield.sa}"

# Load .env
source .env 2>/dev/null || true

echo "Registering webhooks for $BASE_URL ..."

# ── Telegram webhook ─────────────────────────────────────────
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo -n "Telegram ... "
  curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
    -d "url=$BASE_URL/webhook/telegram" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d.get('ok') else d.get('description','FAIL'))"
else
  echo "Telegram: TELEGRAM_BOT_TOKEN not set — skip"
fi

echo ""
echo "════════════════════════════════════════"
echo " Webhook URLs:"
echo "   Telegram:     $BASE_URL/webhook/telegram"
echo "   WhatsApp:     $BASE_URL/webhook/whatsapp"
echo "   LinkedIn:     $BASE_URL/webhook/linkedin"
echo "   Google Forms: $BASE_URL/webhook/google-forms"
echo "   Website:      $BASE_URL/webhook/website"
echo "   Ads:          $BASE_URL/webhook/ads"
echo ""
echo " Dashboard:      $BASE_URL/dashboard"
echo " CPQ:            $BASE_URL/api/cpq/quote"
echo " Health:         $BASE_URL/health"
echo "════════════════════════════════════════"
