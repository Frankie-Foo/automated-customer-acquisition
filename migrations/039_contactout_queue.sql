CREATE TABLE IF NOT EXISTS contactout_accounts (
  id BIGSERIAL PRIMARY KEY,
  account_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  masked_identity TEXT NOT NULL,
  credential_ref TEXT NOT NULL UNIQUE,
  assigned_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active',
  daily_limit INTEGER NOT NULL DEFAULT 5 CHECK (daily_limit >= 0),
  cooldown_until TIMESTAMPTZ,
  authorized_by_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  authorized_at TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (status IN ('active', 'disabled', 'reauth_required', 'challenge_required')),
  CHECK (credential_ref <> '')
);

CREATE TABLE IF NOT EXISTS provider_account_daily_usage (
  provider TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
  reserved_units INTEGER NOT NULL DEFAULT 0 CHECK (reserved_units >= 0),
  used_units INTEGER NOT NULL DEFAULT 0 CHECK (used_units >= 0),
  denied_count INTEGER NOT NULL DEFAULT 0 CHECK (denied_count >= 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (provider, scope_key, usage_date)
);

CREATE TABLE IF NOT EXISTS contactout_enrichment_jobs (
  id BIGSERIAL PRIMARY KEY,
  idempotency_key CHAR(64) NOT NULL UNIQUE,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE CASCADE,
  account_id BIGINT NOT NULL REFERENCES contactout_accounts(id) ON DELETE RESTRICT,
  operation TEXT NOT NULL DEFAULT 'person_enrich',
  input_hash CHAR(64) NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  priority INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
  quota_units INTEGER NOT NULL DEFAULT 1 CHECK (quota_units > 0),
  lease_token TEXT,
  lease_expires_at TIMESTAMPTZ,
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (status IN ('queued', 'running', 'succeeded', 'no_match', 'retry_wait', 'blocked', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_contactout_jobs_due
  ON contactout_enrichment_jobs(status, next_attempt_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_contactout_jobs_owner_created
  ON contactout_enrichment_jobs(owner_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS contactout_enrichment_results (
  id BIGSERIAL PRIMARY KEY,
  job_id BIGINT NOT NULL UNIQUE REFERENCES contactout_enrichment_jobs(id) ON DELETE CASCADE,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  match_status TEXT NOT NULL,
  match_confidence INTEGER NOT NULL DEFAULT 0 CHECK (match_confidence BETWEEN 0 AND 100),
  review_required BOOLEAN NOT NULL DEFAULT FALSE,
  profile_url TEXT,
  email_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
  phone_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE contactout_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE contactout_accounts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contactout_accounts_scope ON contactout_accounts;
CREATE POLICY contactout_accounts_scope ON contactout_accounts
FOR SELECT USING (
  sales_actor_role() IN ('admin', 'system')
  OR assigned_user_id = sales_actor_id()
);
DROP POLICY IF EXISTS contactout_accounts_admin_write ON contactout_accounts;
CREATE POLICY contactout_accounts_admin_write ON contactout_accounts
FOR ALL USING (sales_actor_role() IN ('admin', 'system'))
WITH CHECK (sales_actor_role() IN ('admin', 'system'));

ALTER TABLE provider_account_daily_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_account_daily_usage FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS provider_account_usage_scope ON provider_account_daily_usage;
CREATE POLICY provider_account_usage_scope ON provider_account_daily_usage
FOR ALL USING (sales_actor_role() IN ('admin', 'system'))
WITH CHECK (sales_actor_role() IN ('admin', 'system'));

ALTER TABLE contactout_enrichment_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE contactout_enrichment_jobs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contactout_jobs_read ON contactout_enrichment_jobs;
CREATE POLICY contactout_jobs_read ON contactout_enrichment_jobs
FOR SELECT USING (
  sales_actor_role() IN ('admin', 'system')
  OR owner_user_id = sales_actor_id()
);
DROP POLICY IF EXISTS contactout_jobs_insert ON contactout_enrichment_jobs;
CREATE POLICY contactout_jobs_insert ON contactout_enrichment_jobs
FOR INSERT
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR owner_user_id = sales_actor_id()
);
DROP POLICY IF EXISTS contactout_jobs_write ON contactout_enrichment_jobs;
CREATE POLICY contactout_jobs_write ON contactout_enrichment_jobs
FOR UPDATE USING (sales_actor_role() IN ('admin', 'system'))
WITH CHECK (sales_actor_role() IN ('admin', 'system'));

ALTER TABLE contactout_enrichment_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE contactout_enrichment_results FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contactout_results_scope ON contactout_enrichment_results;
CREATE POLICY contactout_results_scope ON contactout_enrichment_results
FOR SELECT USING (
  sales_actor_role() IN ('admin', 'system')
  OR EXISTS (
    SELECT 1 FROM contactout_enrichment_jobs job
    WHERE job.id = job_id AND job.owner_user_id = sales_actor_id()
  )
);
DROP POLICY IF EXISTS contactout_results_write ON contactout_enrichment_results;
CREATE POLICY contactout_results_write ON contactout_enrichment_results
FOR ALL USING (sales_actor_role() IN ('admin', 'system'))
WITH CHECK (sales_actor_role() IN ('admin', 'system'));
