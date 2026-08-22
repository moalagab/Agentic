"""
Leads Admin Dashboard — لوحة إدارة العملاء يدوياً
CRUD interface for managing leads: view, search, update stage, add notes, delete.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

STAGE_LABELS = {
    "NEW_LEAD":       ("جديد",        "#64748b"),
    "QUALIFIED":      ("مؤهَّل",       "#3b82f6"),
    "CONTACTED":      ("تم التواصل",  "#8b5cf6"),
    "MEETING_BOOKED": ("اجتماع",      "#f59e0b"),
    "PROPOSAL_SENT":  ("عرض مرسل",   "#f97316"),
    "NEGOTIATION":    ("تفاوض",       "#ec4899"),
    "WON":            ("✅ فاز",       "#22c55e"),
    "LOST":           ("❌ خسارة",     "#ef4444"),
    # legacy
    "lead":               ("جديد",     "#64748b"),
    "new":                ("جديد",     "#64748b"),
    "contacted":          ("تواصل",    "#8b5cf6"),
    "qualified":          ("مؤهَّل",    "#3b82f6"),
    "won":                ("✅ فاز",    "#22c55e"),
    "lost":               ("❌ خسارة",  "#ef4444"),
}

PRIORITY_LABELS = {
    "high":   ("● عالية",    "#ef4444"),
    "medium": ("◑ متوسطة",   "#f59e0b"),
    "low":    ("○ منخفضة",   "#22c55e"),
}

SOURCE_LABELS = {
    "serpapi_prospecting": "خرائط جوجل",
    "website":  "الموقع",
    "whatsapp": "واتساب",
    "linkedin": "LinkedIn",
    "manual":   "يدوي",
    "ads":      "إعلانات",
    "referral": "إحالة",
}


def _stage_badge(stage: str) -> str:
    label, color = STAGE_LABELS.get(stage, (stage, "#94a3b8"))
    return f'<span style="background:{color};color:white;padding:3px 10px;border-radius:12px;font-size:11px;white-space:nowrap">{label}</span>'


def _priority_badge(priority: str) -> str:
    label, color = PRIORITY_LABELS.get(priority.lower(), (priority, "#94a3b8"))
    return f'<span style="color:{color};font-weight:600;font-size:13px">{label}</span>'


def _esc(value) -> str:
    """هروب HTML لأي قيمة قادمة من بيانات العميل.

    اسم العميل وشركته وملاحظاته تصل من نماذج الموقع وواتساب — أي أنها
    مُدخَل من الخارج. حقنها الخام في HTML سمح بتنفيذ JavaScript في متصفح
    من يفتح اللوحة (XSS مخزَّن).
    """
    return html.escape(str(value if value is not None else ""), quote=True)


def _js(value) -> str:
    """هروب قيمة توضع داخل نص JavaScript **بداخل خاصية HTML** مثل onclick.

    السياق مزدوج، فيلزم هروبان بالترتيب:
      1. هروب JavaScript  — حتى لا تُنهي القيمةُ نصَّ الـ JS
      2. هروب خاصية HTML — حتى لا تُنهي علامةُ الاقتباس المزدوجة الخاصيةَ
         نفسها (`onclick="..."`) وتُحقن معالج أحداث جديد
    الاكتفاء بالأول يترك الثغرة مفتوحة عبر المحرف `"`.
    """
    js = json.dumps(str(value if value is not None else ""))[1:-1].replace("'", "\\'")
    return html.escape(js, quote=True)


# تسميات الشرائح بالعربية + لون لكل شريحة
ICP_LABELS: dict[str, tuple[str, str]] = {
    "premium_fb":        ("أغذية فاخرة", "#7c3aed"),
    "horeca":            ("فنادق ومطاعم", "#0891b2"),
    "fresh_food":        ("طازج", "#059669"),
    "meal_subscription": ("Meal Run", "#d97706"),
    "not_icp":           ("خارج النطاق", "#94a3b8"),
}


def _icp_badge(segment: str, score: int) -> str:
    """شارة الشريحة مع النتيجة. الشريحة هي المعلومة، والرقم يرتّب داخلها."""
    label, color = ICP_LABELS.get(str(segment or ""), ("—", "#94a3b8"))
    if label == "—":
        return '<span style="color:#cbd5e1">—</span>'
    return (
        f'<span style="background:{color}18;color:{color};border:1px solid {color}44;'
        f'padding:2px 7px;border-radius:10px;font-size:11px;white-space:nowrap">'
        f'{html.escape(label)} {int(score)}</span>'
    )


def _wa_link(phone: str) -> str:
    if not phone:
        return "—"
    clean = phone.replace("+", "").replace(" ", "")
    return f'<a href="https://wa.me/{html.escape(clean, quote=True)}" target="_blank" style="color:#25d366;text-decoration:none">📱 {html.escape(str(phone), quote=True)}</a>'


def render_leads_admin(leads: list[dict], search: str = "", stage_filter: str = "") -> str:
    """Render the full admin HTML page."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    total = len(leads)

    # Counts by stage for summary bar
    stage_counts: dict[str, int] = {}
    for l in leads:
        s = str(l.get("deal_stage") or l.get("status") or "NEW_LEAD").upper()
        stage_counts[s] = stage_counts.get(s, 0) + 1

    summary_pills = ""
    for stage_key in ["NEW_LEAD", "QUALIFIED", "CONTACTED", "MEETING_BOOKED", "PROPOSAL_SENT", "NEGOTIATION", "WON", "LOST"]:
        cnt = stage_counts.get(stage_key, 0)
        label, color = STAGE_LABELS.get(stage_key, (stage_key, "#64748b"))
        summary_pills += f'<span style="background:{color}22;color:{color};border:1px solid {color}44;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;cursor:pointer" onclick="filterStage(\'{stage_key}\')">{label}: {cnt}</span>'

    # Table rows
    rows = ""
    for i, l in enumerate(leads, 1):
        # كل قيمة قادمة من بيانات العميل تُهرَّب قبل وضعها في HTML
        lid       = _esc(l.get("id", ""))
        name      = _esc(l.get("name", "—"))
        company   = _esc(l.get("company") or "—")
        phone     = l.get("phone") or ""
        source    = _esc(SOURCE_LABELS.get(str(l.get("source") or ""), str(l.get("source") or "—")))
        stage     = _esc(str(l.get("deal_stage") or l.get("status") or "NEW_LEAD"))
        priority  = _esc(str(l.get("priority") or "medium").lower())
        score     = int(l.get("score") or 0)
        icp_score = int(l.get("icp_score") or 0)
        icp_seg   = str(l.get("icp_segment") or "")
        icp_badge = _icp_badge(icp_seg, icp_score)
        notes     = _esc((l.get("notes") or "")[:60])
        # قيم مخصّصة للسياق داخل نص JavaScript (onclick)
        js_lid, js_name  = _js(l.get("id", "")), _js(l.get("name", ""))
        js_stage, js_pri = _js(stage), _js(priority)
        rev       = float(l.get("expected_monthly_revenue") or 0)
        created   = str(l.get("created_at") or "")[:10]

        row_stage = stage.upper()
        row_bg = "background:#fff8f8" if row_stage == "LOST" else ("background:#f0fdf4" if row_stage == "WON" else "")

        rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9;{row_bg}" data-stage="{row_stage}" data-id="{lid}">
          <td style="padding:10px 8px;color:#94a3b8;font-size:12px">{i}</td>
          <td style="padding:10px 8px">
            <div style="font-weight:600;font-size:14px">{name}</div>
            <div style="font-size:12px;color:#64748b">{company}</div>
          </td>
          <td style="padding:10px 8px">{_wa_link(phone)}</td>
          <td style="padding:10px 8px;font-size:12px;color:#64748b">{source}</td>
          <td style="padding:10px 8px">{_stage_badge(stage)}</td>
          <td style="padding:10px 8px">{_priority_badge(priority)}</td>
          <td style="padding:10px 8px;text-align:center">
            <span style="background:#3b82f6;color:white;padding:2px 7px;border-radius:10px;font-size:11px">{score}</span>
          </td>
          <td style="padding:10px 8px;text-align:center">{icp_badge}</td>
          <td style="padding:10px 8px;text-align:right;font-size:13px;color:#059669">{int(rev):,}</td>
          <td style="padding:10px 8px;font-size:12px;color:#475569;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{notes}</td>
          <td style="padding:10px 8px;font-size:11px;color:#94a3b8">{created}</td>
          <td style="padding:10px 8px">
            <div style="display:flex;gap:4px">
              <button onclick="openEdit('{js_lid}','{js_name}','{js_stage}','{js_pri}')" style="background:#3b82f6;color:white;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:11px">✏️ تعديل</button>
              <button onclick="deleteLead('{js_lid}','{js_name}')" style="background:#ef4444;color:white;border:none;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:11px">🗑️</button>
            </div>
          </td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="12" style="text-align:center;padding:40px;color:#94a3b8">لا توجد عملاء — ابدأ التنقيب من خرائط جوجل</td></tr>'

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Field — إدارة العملاء</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;color:#1e293b;direction:rtl;font-size:14px}}
  .nav{{background:linear-gradient(135deg,#0f172a,#1e40af);color:white;padding:14px 24px;display:flex;justify-content:space-between;align-items:center;gap:16px}}
  .nav h1{{font-size:16px;font-weight:700;white-space:nowrap}}
  .nav a{{color:rgba(255,255,255,.8);text-decoration:none;font-size:13px}}
  .container{{max-width:1400px;margin:0 auto;padding:16px}}
  .toolbar{{background:white;border-radius:10px;padding:14px 16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
  .summary{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}}
  input[type=text]{{border:1px solid #e2e8f0;border-radius:8px;padding:8px 14px;font-size:13px;outline:none;min-width:220px;direction:rtl}}
  input[type=text]:focus{{border-color:#3b82f6}}
  select{{border:1px solid #e2e8f0;border-radius:8px;padding:8px 12px;font-size:13px;outline:none;direction:rtl;background:white}}
  button.btn{{border:none;border-radius:8px;padding:8px 16px;cursor:pointer;font-size:13px;font-weight:600}}
  .btn-blue{{background:#3b82f6;color:white}}
  .btn-green{{background:#22c55e;color:white}}
  .btn-gray{{background:#f1f5f9;color:#475569}}
  table{{width:100%;border-collapse:collapse;background:white;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.06);overflow:hidden}}
  th{{background:#f8fafc;color:#64748b;padding:10px 8px;font-size:11px;font-weight:700;text-align:right;border-bottom:2px solid #e2e8f0;white-space:nowrap}}
  tr:hover td{{background:#f8fafc}}
  .modal{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:1000;align-items:center;justify-content:center}}
  .modal.open{{display:flex}}
  .modal-box{{background:white;border-radius:12px;padding:24px;width:420px;max-width:95vw;direction:rtl}}
  .modal-box h3{{font-size:16px;font-weight:700;margin-bottom:16px}}
  .form-row{{margin-bottom:12px}}
  .form-row label{{font-size:12px;color:#64748b;display:block;margin-bottom:4px}}
  .form-row select,.form-row textarea,.form-row input{{width:100%;border:1px solid #e2e8f0;border-radius:8px;padding:8px 12px;font-size:13px;direction:rtl}}
  .form-row textarea{{resize:vertical;min-height:70px}}
  .modal-actions{{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}}
  .toast{{position:fixed;bottom:24px;right:24px;background:#1e293b;color:white;padding:12px 20px;border-radius:8px;font-size:13px;z-index:2000;display:none}}
</style>
</head>
<body>

<div class="nav">
  <h1>❄️ Smart Field — إدارة العملاء</h1>
  <div style="display:flex;gap:16px;align-items:center">
    <span style="font-size:13px;opacity:.7">{total} عميل | {now}</span>
    <a href="/dashboard">📊 Dashboard</a>
    <a href="/admin/leads">🔄 تحديث</a>
  </div>
</div>

<div class="container">

  <!-- Summary pills -->
  <div class="summary">{summary_pills}</div>

  <!-- Toolbar -->
  <div class="toolbar">
    <input type="text" id="search-input" placeholder="🔍 ابحث بالاسم أو الهاتف..." oninput="filterTable()">
    <select id="stage-select" onchange="filterTable()">
      <option value="">كل المراحل</option>
      <option value="NEW_LEAD">جديد</option>
      <option value="QUALIFIED">مؤهَّل</option>
      <option value="CONTACTED">تم التواصل</option>
      <option value="MEETING_BOOKED">اجتماع محدد</option>
      <option value="PROPOSAL_SENT">عرض مرسل</option>
      <option value="NEGOTIATION">تفاوض</option>
      <option value="WON">فاز ✅</option>
      <option value="LOST">خسارة ❌</option>
    </select>
    <select id="priority-select" onchange="filterTable()">
      <option value="">كل الأولويات</option>
      <option value="high">عالية</option>
      <option value="medium">متوسطة</option>
      <option value="low">منخفضة</option>
    </select>
    <button class="btn btn-green" onclick="triggerProspecting()">🔍 تنقيب جديد</button>
    <span id="count-label" style="font-size:12px;color:#64748b;margin-right:auto"></span>
  </div>

  <!-- Leads Table -->
  <table id="leads-table">
    <thead><tr>
      <th>#</th>
      <th>الاسم / الشركة</th>
      <th>الهاتف</th>
      <th>المصدر</th>
      <th>المرحلة</th>
      <th>الأولوية</th>
      <th>Score</th>
      <th>ICP</th>
      <th>إيراد/شهر</th>
      <th>ملاحظات</th>
      <th>التاريخ</th>
      <th>إجراء</th>
    </tr></thead>
    <tbody id="leads-body">{rows}</tbody>
  </table>

</div>

<!-- Edit Modal -->
<div class="modal" id="edit-modal">
  <div class="modal-box">
    <h3>✏️ تعديل العميل</h3>
    <input type="hidden" id="edit-id">
    <div class="form-row">
      <label>اسم العميل</label>
      <input type="text" id="edit-name" readonly style="background:#f8fafc">
    </div>
    <div class="form-row">
      <label>مرحلة الصفقة</label>
      <select id="edit-stage">
        <option value="NEW_LEAD">جديد</option>
        <option value="QUALIFIED">مؤهَّل</option>
        <option value="CONTACTED">تم التواصل</option>
        <option value="MEETING_BOOKED">اجتماع محدد</option>
        <option value="PROPOSAL_SENT">عرض مرسل</option>
        <option value="NEGOTIATION">تفاوض</option>
        <option value="WON">فاز ✅</option>
        <option value="LOST">خسارة ❌</option>
      </select>
    </div>
    <div class="form-row">
      <label>الأولوية</label>
      <select id="edit-priority">
        <option value="high">عالية</option>
        <option value="medium">متوسطة</option>
        <option value="low">منخفضة</option>
      </select>
    </div>
    <div class="form-row">
      <label>الإيراد الشهري المتوقع (ريال)</label>
      <input type="number" id="edit-revenue" min="0" placeholder="0">
    </div>
    <div class="form-row">
      <label>احتمال الإغلاق %</label>
      <input type="number" id="edit-probability" min="0" max="100" placeholder="0">
    </div>
    <div class="form-row">
      <label>ملاحظات</label>
      <textarea id="edit-notes" placeholder="أضف ملاحظة..."></textarea>
    </div>
    <div class="modal-actions">
      <button class="btn btn-gray" onclick="closeModal()">إلغاء</button>
      <button class="btn btn-blue" onclick="saveEdit()">💾 حفظ</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
function filterStage(stage) {{
  document.getElementById('stage-select').value = stage;
  filterTable();
}}

function filterTable() {{
  const search   = document.getElementById('search-input').value.toLowerCase();
  const stage    = document.getElementById('stage-select').value.toUpperCase();
  const priority = document.getElementById('priority-select').value.toLowerCase();
  const rows = document.querySelectorAll('#leads-body tr');
  let visible = 0;
  rows.forEach(row => {{
    const text = row.textContent.toLowerCase();
    const rowStage = (row.dataset.stage || '').toUpperCase();
    const rowPriority = (row.querySelector('td:nth-child(6)')?.textContent || '').toLowerCase();

    const matchSearch   = !search   || text.includes(search);
    const matchStage    = !stage    || rowStage === stage;
    const matchPriority = !priority || rowPriority.includes(priority === 'high' ? 'عالية' : priority === 'medium' ? 'متوسطة' : 'منخفضة');

    if (matchSearch && matchStage && matchPriority) {{
      row.style.display = '';
      visible++;
    }} else {{
      row.style.display = 'none';
    }}
  }});
  document.getElementById('count-label').textContent = visible + ' من ' + rows.length;
}}

function openEdit(id, name, stage, priority) {{
  document.getElementById('edit-id').value = id;
  document.getElementById('edit-name').value = name;
  document.getElementById('edit-stage').value = stage.toUpperCase();
  document.getElementById('edit-priority').value = priority.toLowerCase();
  document.getElementById('edit-notes').value = '';
  document.getElementById('edit-revenue').value = '';
  document.getElementById('edit-probability').value = '';
  document.getElementById('edit-modal').classList.add('open');
}}

function closeModal() {{
  document.getElementById('edit-modal').classList.remove('open');
}}

async function saveEdit() {{
  const id       = document.getElementById('edit-id').value;
  const stage    = document.getElementById('edit-stage').value;
  const priority = document.getElementById('edit-priority').value;
  const notes    = document.getElementById('edit-notes').value;
  const revenue  = document.getElementById('edit-revenue').value;
  const prob     = document.getElementById('edit-probability').value;

  try {{
    // Update stage
    await fetch('/api/lead/' + id + '/stage', {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{stage, changed_by:'admin'}})
    }});

    // Update other fields
    const updates = {{priority}};
    if (notes) updates.notes = notes;
    if (revenue) updates.expected_monthly_revenue = parseFloat(revenue);
    if (prob) updates.expected_close_probability = parseFloat(prob) / 100;

    await fetch('/api/lead/' + id + '/update', {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify(updates)
    }});

    showToast('✅ تم الحفظ بنجاح');
    closeModal();
    setTimeout(() => location.reload(), 1000);
  }} catch(e) {{
    showToast('❌ حدث خطأ: ' + e.message);
  }}
}}

async function deleteLead(id, name) {{
  if (!confirm('حذف العميل: ' + name + '؟')) return;
  try {{
    const r = await fetch('/api/lead/' + id, {{method:'DELETE'}});
    const d = await r.json();
    if (d.success) {{
      showToast('🗑️ تم الحذف');
      setTimeout(() => location.reload(), 800);
    }} else {{
      showToast('❌ فشل الحذف');
    }}
  }} catch(e) {{
    showToast('❌ خطأ: ' + e.message);
  }}
}}

async function triggerProspecting() {{
  showToast('🔍 جاري التنقيب...');
  try {{
    const r = await fetch('/api/prospecting/run', {{method:'POST'}});
    const d = await r.json();
    showToast('✅ تم التنقيب: ' + (d.found || 0) + ' عميل جديد');
  }} catch(e) {{
    showToast('❌ خطأ في التنقيب');
  }}
}}

function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}}

// Init count
filterTable();
</script>
</body>
</html>"""
