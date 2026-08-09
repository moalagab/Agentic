# قائمة فحص التعطل — Smart Field IROS

> النظام توقف بالكامل في `2026-07-22` (كل الجداول في Supabase توقفت في نفس
> اليوم بالضبط: leads، lead_events، activities، deal_stage_history) ولم يصل
> أي إشعار. هذه القائمة لتشخيص السبب واستعادة النظام. نُفَّذ على السيرفر
> مباشرة (SSH) — لا يمكن تشخيصها من هنا بلا وصول للسيرفر.

## 1. هل الخدمة (systemd) تعمل؟

```bash
sudo systemctl status smartfield.service
```

- **إذا `inactive`/`failed`** → الخدمة متوقفة. شغّلها:
  ```bash
  sudo systemctl start smartfield.service
  sudo systemctl enable smartfield.service   # تأكد أنها تُقلع تلقائيًا بعد أي reboot
  ```
- **إذا `active (running)`** لكن لا نشاط في Supabase → المشكلة ليست في العملية نفسها، انتقل للبند 3.

## 2. لماذا توقفت؟ (السجلات)

```bash
# آخر 200 سطر من سجل التطبيق
tail -200 /home/smartfield/app/logs/app.log

# سجل systemd نفسه — يوضّح متى توقفت العملية ولماذا (crash / OOM / manual stop)
sudo journalctl -u smartfield.service --since "2026-07-21" --until "2026-07-23" --no-pager
```

ابحث تحديدًا عن:
- `OOM` أو `Killed` → نفاد الذاكرة (RAM) — الخادم Hetzner CX22 صغير نسبيًا
- `ANTHROPIC_API_KEY` أو `credit` أو `429` → توقف بسبب رصيد/معدل الطلبات
- استثناء غير مُتوقَّع (Traceback) قبل التوقف مباشرة

## 3. هل WAHA (واتساب) متصل؟

```bash
curl -s http://localhost:3000/api/sessions/default \
  -H "X-Api-Key: $WAHA_API_KEY" | python3 -m json.tool
```

الحالة المتوقعة: `"status": "WORKING"`. إن كانت `STOPPED` أو `FAILED`:
```bash
curl -X POST http://localhost:3000/api/sessions/default/start \
  -H "X-Api-Key: $WAHA_API_KEY"
```
إن كانت `SCAN_QR_CODE` — الجلسة تحتاج مسح QR جديد (ربما انتهت صلاحيتها):
افتح `https://agent.smartfield.sa/qr` وامسحها من واتساب الرقم `0561167169`.

> ملاحظة: `waha_health_check` مجدول كل 5 دقائق ويُفترض أن يعيد الاتصال تلقائيًا
> ويُرسل تنبيه Telegram عند مشاكل QR — لكنه لا يعمل إطلاقًا إذا كانت خدمة
> `smartfield.service` نفسها متوقفة (البند 1)، وهذا الأرجح فعليًا لأن **كل**
> الجداول توقفت معًا بما فيها `serpapi_prospecting` التي لا علاقة لها بواتساب.

## 4. هل السيرفر (Hetzner) نفسه وصل لحدّ الموارد؟

```bash
df -h          # مساحة القرص — قاعدة بيانات memory.db أو logs قد تكون امتلأت القرص
free -h        # الذاكرة
uptime         # هل حدث reboot حديثًا؟ (uptime قصير يعني نعم)
```

## 5. بعد إعادة التشغيل — تأكيد الصحة

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

ثم تحقق من Supabase مباشرة أن `lead_events` بدأ يستقبل صفوفًا جديدة خلال
دقائق (`serpapi_prospecting` يعمل يوميًا — إن لم تظهر صفوف جديدة خلال 24
ساعة، الجدولة (scheduler) لم تُقلع فعليًا رغم أن العملية تعمل).

## 6. لمنع تكرار هذا لاحقًا

- تم إضافة `StartLimitIntervalSec=0` لملف `smartfield.service` (هذه الجلسة) —
  يمنع systemd من الاستسلام بعد عدة محاولات إعادة تشغيل فاشلة في وقت قصير.
  **يحتاج نشر الملف المحدَّث على السيرفر وإعادة تحميله:**
  ```bash
  sudo cp deploy/smartfield.service /etc/systemd/system/smartfield.service
  sudo systemctl daemon-reload
  sudo systemctl restart smartfield.service
  ```
- فكّر بإضافة مراقبة خارجية مستقلة عن العملية نفسها (مثل UptimeRobot/healthchecks.io
  يفحص `GET /health` كل 5 دقائق) — لأن أي مراقبة داخل العملية (مثل `waha_health_check`)
  تموت مع العملية نفسها ولا تستطيع الإبلاغ عن توقفها هي.
