CREATE TABLE IF NOT EXISTS campaigns (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  channel TEXT NOT NULL,
  region TEXT,
  product_line TEXT,
  owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active',
  budget_amount NUMERIC(12, 2),
  currency TEXT NOT NULL DEFAULT 'USD',
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_channel_status
  ON campaigns(channel, status);

CREATE INDEX IF NOT EXISTS idx_campaigns_owner_created
  ON campaigns(owner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS campaign_metrics (
  id BIGSERIAL PRIMARY KEY,
  campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  metric_date DATE NOT NULL DEFAULT CURRENT_DATE,
  leads_count INTEGER NOT NULL DEFAULT 0,
  valid_contacts_count INTEGER NOT NULL DEFAULT 0,
  sent_count INTEGER NOT NULL DEFAULT 0,
  opened_count INTEGER NOT NULL DEFAULT 0,
  replied_count INTEGER NOT NULL DEFAULT 0,
  meeting_count INTEGER NOT NULL DEFAULT 0,
  quoted_count INTEGER NOT NULL DEFAULT 0,
  won_count INTEGER NOT NULL DEFAULT 0,
  lost_count INTEGER NOT NULL DEFAULT 0,
  cost_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
  revenue_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(campaign_id, metric_date)
);

CREATE INDEX IF NOT EXISTS idx_campaign_metrics_date
  ON campaign_metrics(metric_date DESC);

CREATE TABLE IF NOT EXISTS leads (
  id BIGSERIAL PRIMARY KEY,
  external_id TEXT,
  source_type TEXT NOT NULL,
  source_ref TEXT,
  source_row INTEGER,
  campaign_id BIGINT REFERENCES campaigns(id) ON DELETE SET NULL,
  contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
  owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  normalized_email TEXT,
  normalized_phone TEXT,
  normalized_whatsapp TEXT,
  company_domain TEXT,
  country TEXT,
  region TEXT,
  language TEXT,
  dedupe_key TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  quality_score INTEGER,
  failure_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(source_type, external_id)
);

CREATE INDEX IF NOT EXISTS idx_leads_contact_id
  ON leads(contact_id);

CREATE INDEX IF NOT EXISTS idx_leads_campaign_status
  ON leads(campaign_id, status);

CREATE INDEX IF NOT EXISTS idx_leads_owner_status
  ON leads(owner_user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_leads_dedupe_key
  ON leads(dedupe_key);

CREATE INDEX IF NOT EXISTS idx_leads_email
  ON leads(normalized_email);

CREATE INDEX IF NOT EXISTS idx_leads_phone
  ON leads(normalized_phone);

CREATE TABLE IF NOT EXISTS interactions (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
  user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  interaction_type TEXT NOT NULL,
  direction TEXT NOT NULL DEFAULT 'outbound',
  channel TEXT NOT NULL,
  subject TEXT,
  content TEXT,
  outcome TEXT,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source_ref TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interactions_contact_occurred
  ON interactions(contact_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_interactions_user_occurred
  ON interactions(user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_interactions_channel_outcome
  ON interactions(channel, outcome);

CREATE TABLE IF NOT EXISTS followup_tasks (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
  assigned_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  created_by_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  task_type TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'normal',
  title TEXT NOT NULL,
  description TEXT,
  due_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'open',
  completed_at TIMESTAMPTZ,
  trigger_rule TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_followup_tasks_assignee_status_due
  ON followup_tasks(assigned_user_id, status, due_at);

CREATE INDEX IF NOT EXISTS idx_followup_tasks_contact_status
  ON followup_tasks(contact_id, status);

CREATE TABLE IF NOT EXISTS outreach_messages (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
  campaign_id BIGINT REFERENCES campaigns(id) ON DELETE SET NULL,
  user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  draft_id BIGINT REFERENCES email_drafts(id) ON DELETE SET NULL,
  channel TEXT NOT NULL,
  sequence_step INTEGER NOT NULL DEFAULT 1,
  subject TEXT,
  body TEXT NOT NULL,
  language TEXT,
  ai_model TEXT,
  personalization_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL DEFAULT 'draft',
  provider TEXT,
  provider_message_id TEXT,
  sent_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  opened_at TIMESTAMPTZ,
  replied_at TIMESTAMPTZ,
  bounced_at TIMESTAMPTZ,
  error TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outreach_messages_contact_status
  ON outreach_messages(contact_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_outreach_messages_provider_id
  ON outreach_messages(provider, provider_message_id);

CREATE INDEX IF NOT EXISTS idx_outreach_messages_campaign_status
  ON outreach_messages(campaign_id, status);

CREATE OR REPLACE VIEW customer_profiles AS
SELECT
  c.id AS contact_id,
  c.first_name,
  c.last_name,
  c.email,
  c.email_status,
  c.phone,
  c.job_title,
  c.company_name,
  c.company_domain,
  c.industry,
  c.location,
  c.owner_user_id,
  c.owner,
  c.status,
  c.lifecycle_stage,
  c.sabcd_stage,
  c.disposition,
  c.lead_score,
  c.identity_confidence,
  c.identity_status,
  c.profile_summary,
  c.profile_insights,
  c.customer_profile_snapshot,
  c.created_at,
  COALESCE(c.profile_updated_at, c.enriched_at, c.created_at) AS updated_at
FROM contacts c;
