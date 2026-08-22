-- ═══════════════════════════════════════════════════════════════════════════
-- Smart Field — Supabase Schema (complete, extracted from live DB 2026-08-09)
-- Project: smartfield lead agent (trytosmzcerhdukdkgvg)
--
-- This file previously defined only 4 of the 8 tables actually in use — the
-- other 4 (leads, deal_stage_history, notifications_log, lead_events) were
-- created directly against the live database and never captured here. This
-- version was generated from information_schema against the live DB and is
-- a faithful snapshot: same columns, types, defaults, PKs, FKs, and indexes.
--
-- Safe to run against the current live DB — every statement is idempotent
-- (IF NOT EXISTS). Tables are ordered so `leads` (referenced by every other
-- table) is created first.
--
-- NOTE — RLS: all 8 tables have Row Level Security ENABLED but ZERO policies
-- defined. This means only the `service_role` key (which bypasses RLS) can
-- read/write — correct for this server-only backend today, but if a
-- client-facing surface (customer portal, `anon`/`authenticated` key) is ever
-- added, it will get zero rows back until explicit policies are written.
--
-- NOTE — CHECK constraints: the live tables have none (not even the ones an
-- earlier version of this file specified, e.g. conversations.role IN
-- ('user','ai')). Enum-like columns (status, deal_stage, role, type,
-- contract_status, approval_status) are plain TEXT/VARCHAR today — validated
-- only in application code. Constraints are commented out below where a
-- prior version of this file implied them; add them explicitly if you want
-- the DB to enforce them (do NOT uncomment blindly — first audit live data
-- for values that would violate them, since e.g. leads.status/deal_stage
-- currently disagree on 152 rows).
-- ═══════════════════════════════════════════════════════════════════════════


-- ── 1. leads (root table — everything else references this) ────────────────
CREATE TABLE IF NOT EXISTS leads (
  id                          UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
  name                        TEXT           NOT NULL,
  company                     TEXT,
  phone                       TEXT,
  email                       TEXT,
  source                      TEXT           DEFAULT 'manual',
  status                      TEXT           DEFAULT 'new',
  priority                    TEXT           DEFAULT 'medium',
  category                    TEXT           DEFAULT 'other',
  cargo_type                  TEXT,
  route_from                  TEXT,
  route_to                    TEXT,
  fleet_size_needed           INTEGER,
  budget_monthly              NUMERIC,
  notes                       TEXT,
  raw_data                    JSONB          DEFAULT '{}'::jsonb,
  crm_id                      TEXT,
  assigned_to                 TEXT,
  score                       INTEGER        DEFAULT 0,
  follow_up_date              TIMESTAMPTZ,
  classification_reasoning    TEXT,
  next_actions                JSONB          DEFAULT '[]'::jsonb,
  created_at                  TIMESTAMPTZ    DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ    DEFAULT NOW(),
  deal_stage                  TEXT           DEFAULT 'lead',
  deal_stage_updated_at       TIMESTAMPTZ,
  first_response_at           TIMESTAMPTZ,
  response_time_minutes       INTEGER,
  estimated_monthly_revenue   NUMERIC        DEFAULT 0,
  estimated_trips             INTEGER        DEFAULT 0,
  confidence_score            INTEGER        DEFAULT 0,
  probability_to_close        NUMERIC        DEFAULT 0,
  expected_deal_value         NUMERIC        DEFAULT 0,
  followup_count              INTEGER        DEFAULT 0,
  followup_last_sent          TIMESTAMP,
  thread_locked               BOOLEAN        DEFAULT FALSE,
  completed_trips             INTEGER        DEFAULT 0,
  contract_offered            BOOLEAN        DEFAULT FALSE,
  contract_status             VARCHAR(20),
  approval_status             VARCHAR(20)    DEFAULT 'PENDING',

  -- ICP: تُحسَب في التنقيب عبر processors/icp_engine.py
  -- premium_fb | fresh_food | horeca | meal_subscription | not_icp
  -- (pharma_beauty مستبعدة قانونيًا — لا ترخيص ناقل SFDA)
  icp_score                   INTEGER        DEFAULT 0,
  icp_segment                 VARCHAR(32),
  buying_signals              JSONB          DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_deal_stage ON leads(deal_stage);
CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(priority);
CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_icp_segment ON leads(icp_segment);
CREATE INDEX IF NOT EXISTS idx_leads_icp_score   ON leads(icp_score DESC);
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
-- No UNIQUE constraint on phone today — this is why serpapi_prospecting has
-- re-inserted the same ~330 real phone numbers up to 15x each (1,931 of
-- 2,260 rows, 85%, are duplicates). See fix-duplicate-leads task before
-- adding UNIQUE(phone), since live data must be deduped first or the
-- constraint will fail to apply.


-- ── 2. conversations ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  message     TEXT        NOT NULL,
  role        TEXT        NOT NULL, -- was CHECK (role IN ('user','ai')) in a prior draft; not enforced live
  timestamp   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_lead_id ON conversations(lead_id);
CREATE INDEX IF NOT EXISTS idx_conversations_timestamp ON conversations(timestamp DESC);
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
-- Currently 0 rows despite the agent actively messaging customers — the
-- write path exists in schema but isn't called from employee/autonomous_agent.py.


-- ── 3. deals ──────────────────────────────────────────────────────────────────
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
ALTER TABLE deals ENABLE ROW LEVEL SECURITY;
-- Currently 0 rows — no deal has ever been recorded here. The "39,500 SAR
-- pipeline" figure quoted elsewhere comes from leads.estimated_monthly_revenue
-- (an AI estimate on unqualified leads), not from this table.


-- ── 4. activities ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activities (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  type        TEXT        NOT NULL, -- was CHECK (type IN ('call','follow_up','quote','message','meeting')) in a prior draft; not enforced live
  status      TEXT        DEFAULT 'pending',
  notes       TEXT,
  timestamp   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_activities_lead_id ON activities(lead_id);
CREATE INDEX IF NOT EXISTS idx_activities_type ON activities(type);
ALTER TABLE activities ENABLE ROW LEVEL SECURITY;


-- ── 5. deal_stage_history (audit trail for leads.deal_stage transitions) ────
CREATE TABLE IF NOT EXISTS deal_stage_history (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  from_stage  TEXT,
  to_stage    TEXT        NOT NULL,
  changed_at  TIMESTAMPTZ DEFAULT NOW(),
  changed_by  TEXT        DEFAULT 'system',
  notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_deal_history_lead ON deal_stage_history(lead_id);
ALTER TABLE deal_stage_history ENABLE ROW LEVEL SECURITY;


-- ── 6. notifications_log ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications_log (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  channel     TEXT        NOT NULL,
  recipient   TEXT,
  status      TEXT        DEFAULT 'sent',
  payload     JSONB       DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE notifications_log ENABLE ROW LEVEL SECURITY;
-- Was stuck at 0 rows — the table existed but no code path ever wrote to
-- it. Wired 2026-08-09: crm.log_notification() is now called from
-- agent/core.py and processors/pipeline.py right after a new-lead
-- Telegram/WhatsApp alert is sent to the sales team.


-- ── 7. lead_events (event-sourcing style audit log per lead) ────────────────
CREATE TABLE IF NOT EXISTS lead_events (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id     UUID        REFERENCES leads(id) ON DELETE CASCADE,
  event_type  TEXT        NOT NULL,
  data        JSONB       DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE lead_events ENABLE ROW LEVEL SECURITY;
-- Live data (as of 2026-08-09, before this fix): 2,268 rows, 2,260 of them
-- event_type='created' and only 8 'task_created' — nothing about what
-- happens to a lead afterward was ever captured. Wired 2026-08-09:
-- 'stage_changed' is now logged from crm.update_deal_stage() (every
-- caller: manual notes, stage_tracker, followup_engine's auto-lost path),
-- and 'message_sent'/'reply_received' from the WhatsApp webhook's
-- _auto_track_stage() choke point, where phone→lead_id is already resolved.


-- ── 8. outbound_leads (cold-outreach prospecting queue, separate from leads) ─
CREATE TABLE IF NOT EXISTS outbound_leads (
  id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  company_name     TEXT        NOT NULL,
  industry         TEXT,
  contact          TEXT,
  phone            TEXT,
  email            TEXT,
  city             TEXT,
  status           TEXT        DEFAULT 'new', -- was CHECK (status IN ('new','contacted','replied','converted','disqualified')) in a prior draft; not enforced live
  score            INTEGER     DEFAULT 0,
  source           TEXT        DEFAULT 'prospecting',
  outreach_message TEXT,
  raw_data         JSONB       DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_outbound_leads_status ON outbound_leads(status);
CREATE INDEX IF NOT EXISTS idx_outbound_leads_industry ON outbound_leads(industry);
ALTER TABLE outbound_leads ENABLE ROW LEVEL SECURITY;
