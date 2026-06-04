-- ═══════════════════════════════════════════════════════════
-- Smart Field — Supabase Schema
-- شغّل هذا في: Supabase Dashboard → SQL Editor → New Query
-- ═══════════════════════════════════════════════════════════


-- ── 1. conversations ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  message     TEXT        NOT NULL,
  role        TEXT        NOT NULL CHECK (role IN ('user', 'ai')),
  timestamp   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_lead_id ON conversations(lead_id);
CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp DESC);


-- ── 2. deals ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deals (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  value       NUMERIC(12,2) DEFAULT 0,
  stage       TEXT        DEFAULT 'prospecting',
  probability NUMERIC(5,2) DEFAULT 0,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deals_lead_id ON deals(lead_id);
CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage);


-- ── 3. activities ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activities (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  type        TEXT        NOT NULL CHECK (type IN ('call', 'follow_up', 'quote', 'message', 'meeting')),
  status      TEXT        DEFAULT 'pending',
  notes       TEXT,
  timestamp   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_activities_lead_id ON activities(lead_id);
CREATE INDEX IF NOT EXISTS idx_activities_type ON activities(type);


-- ── 4. outbound_leads ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS outbound_leads (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  company_name     TEXT        NOT NULL,
  industry         TEXT,
  contact          TEXT,
  phone            TEXT,
  email            TEXT,
  city             TEXT,
  status           TEXT        DEFAULT 'new' CHECK (status IN ('new', 'contacted', 'replied', 'converted', 'disqualified')),
  score            INTEGER     DEFAULT 0,
  source           TEXT        DEFAULT 'prospecting',
  outreach_message TEXT,
  raw_data         JSONB       DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_outbound_leads_status ON outbound_leads(status);
CREATE INDEX IF NOT EXISTS idx_outbound_leads_industry ON outbound_leads(industry);
