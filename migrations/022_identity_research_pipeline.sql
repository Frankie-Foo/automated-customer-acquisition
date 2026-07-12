ALTER TABLE lead_search_results
  ADD COLUMN IF NOT EXISTS match_confidence INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS match_status TEXT NOT NULL DEFAULT 'review',
  ADD COLUMN IF NOT EXISTS match_evidence JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS identity_confidence INTEGER,
  ADD COLUMN IF NOT EXISTS identity_status TEXT,
  ADD COLUMN IF NOT EXISTS identity_evidence JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS contact_research (
  contact_id BIGINT PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
  summary TEXT,
  company_signals JSONB NOT NULL DEFAULT '[]'::jsonb,
  person_signals JSONB NOT NULL DEFAULT '[]'::jsonb,
  news_signals JSONB NOT NULL DEFAULT '[]'::jsonb,
  sources JSONB NOT NULL DEFAULT '[]'::jsonb,
  provider TEXT,
  researched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_contact_research_expiry
  ON contact_research(expires_at);

CREATE TABLE IF NOT EXISTS email_drafts (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  sequence_step SMALLINT NOT NULL DEFAULT 1,
  mode TEXT NOT NULL DEFAULT 'ai',
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  research_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_email_drafts_contact_created
  ON email_drafts(contact_id, created_at DESC);

CREATE TABLE IF NOT EXISTS outbound_send_attempts (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  sequence_step SMALLINT NOT NULL,
  user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  provider TEXT NOT NULL,
  sender_email TEXT,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'sending',
  message_id TEXT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(contact_id, sequence_step),
  UNIQUE(idempotency_key)
);
