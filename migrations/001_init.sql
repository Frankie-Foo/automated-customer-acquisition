CREATE TYPE contact_status AS ENUM (
  'new', 'enriched', 'queued', 'sent_1', 'sent_2', 'sent_3',
  'replied', 'bounced', 'unsubscribed'
);

CREATE TYPE email_event_type AS ENUM (
  'sent', 'opened', 'clicked', 'replied', 'bounced', 'unsubscribed'
);

CREATE TABLE contacts (
  id BIGSERIAL PRIMARY KEY,
  linkedin_url TEXT UNIQUE NOT NULL,
  first_name TEXT,
  last_name TEXT,
  email TEXT,
  email_status TEXT DEFAULT 'unknown',
  job_title TEXT,
  company_name TEXT,
  company_domain TEXT,
  company_size TEXT,
  company_funding TEXT,
  industry TEXT,
  location TEXT,
  status contact_status NOT NULL DEFAULT 'new',
  sequence_step INTEGER NOT NULL DEFAULT 0,
  last_contacted_at TIMESTAMPTZ,
  replied_at TIMESTAMPTZ,
  enriched_at TIMESTAMPTZ,
  enrich_error TEXT,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source TEXT
);

CREATE TABLE email_events (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  sequence_step SMALLINT NOT NULL,
  event_type email_event_type NOT NULL,
  email_subject TEXT,
  message_id TEXT,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE blacklist (
  id BIGSERIAL PRIMARY KEY,
  email TEXT UNIQUE,
  domain TEXT UNIQUE,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (email IS NOT NULL OR domain IS NOT NULL)
);

CREATE TABLE webhook_events (
  id BIGSERIAL PRIMARY KEY,
  provider TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  processed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_contacts_status_created ON contacts(status, created_at);
CREATE INDEX idx_contacts_email ON contacts(email);
CREATE INDEX idx_contacts_domain ON contacts(company_domain);
CREATE INDEX idx_email_events_contact_type ON email_events(contact_id, event_type);
CREATE UNIQUE INDEX idx_email_sent_idempotency
  ON email_events(contact_id, sequence_step, event_type)
  WHERE event_type = 'sent';

