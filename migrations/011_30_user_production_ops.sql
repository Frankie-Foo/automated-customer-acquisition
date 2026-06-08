ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_owner_user_status
  ON contacts(owner_user_id, status, created_at);

CREATE TABLE IF NOT EXISTS email_provider_stats (
  provider TEXT NOT NULL,
  stat_date DATE NOT NULL DEFAULT CURRENT_DATE,
  calls INTEGER NOT NULL DEFAULT 0,
  candidates INTEGER NOT NULL DEFAULT 0,
  valid_candidates INTEGER NOT NULL DEFAULT 0,
  selected INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  credits_used INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (provider, stat_date)
);

CREATE INDEX IF NOT EXISTS idx_email_provider_stats_date
  ON email_provider_stats(stat_date);
