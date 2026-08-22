-- ═══════════════════════════════════════════════════════════════════
-- إضافة أعمدة ICP إلى جدول leads
--
-- محرّك ICP كان يحسب الشريحة والنتيجة لكل عميل عند التنقيب، لكن
-- _lead_to_row لم تكن تمرّرها والأعمدة غير موجودة أصلًا — فكان
-- التصنيف يضيع بصمت بلا خطأ ولا تحذير. أثر ذلك: عمود ICP في لوحة
-- الإدارة وبطاقات الموافقة يعرض "—" دائمًا، ولا تحليل تاريخي ممكن.
--
-- آمن للتشغيل أكثر من مرة (IF NOT EXISTS).
-- ═══════════════════════════════════════════════════════════════════

ALTER TABLE leads ADD COLUMN IF NOT EXISTS icp_score      INTEGER      DEFAULT 0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS icp_segment    VARCHAR(32);
ALTER TABLE leads ADD COLUMN IF NOT EXISTS buying_signals JSONB        DEFAULT '[]'::jsonb;

-- فهرس للاستعلام حسب الشريحة (لوحات التحليل وتقارير الشرائح)
CREATE INDEX IF NOT EXISTS idx_leads_icp_segment ON leads(icp_segment);
CREATE INDEX IF NOT EXISTS idx_leads_icp_score   ON leads(icp_score DESC);

-- توثيق القيم المسموحة. pharma_beauty مستبعدة قانونيًا (لا ترخيص
-- ناقل SFDA) فلا تُنتَج من محرّك التقييم، لكنها تبقى ضمن القيم
-- المعروفة لأن النموذج يعرّفها.
COMMENT ON COLUMN leads.icp_segment IS
  'premium_fb | fresh_food | horeca | meal_subscription | not_icp | (pharma_beauty — مستبعدة)';
COMMENT ON COLUMN leads.icp_score IS 'نتيجة مطابقة ICP 0-100؛ أقل من 20 ⇒ not_icp';
