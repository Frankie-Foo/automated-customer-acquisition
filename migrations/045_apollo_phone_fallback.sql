ALTER TABLE sales_users
  ADD COLUMN IF NOT EXISTS apollo_daily_credit_limit INTEGER NOT NULL DEFAULT 0;

ALTER TABLE sales_users
  DROP CONSTRAINT IF EXISTS sales_users_apollo_daily_credit_limit_check;
ALTER TABLE sales_users
  ADD CONSTRAINT sales_users_apollo_daily_credit_limit_check
  CHECK (apollo_daily_credit_limit >= 0);

CREATE TABLE IF NOT EXISTS apollo_phone_enrichment_jobs (
  id BIGSERIAL PRIMARY KEY,
  idempotency_key CHAR(64) NOT NULL UNIQUE,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  owner_user_id BIGINT NOT NULL REFERENCES sales_users(id) ON DELETE CASCADE,
  input_hash CHAR(64) NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  priority INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  quota_units INTEGER NOT NULL DEFAULT 9 CHECK (quota_units BETWEEN 1 AND 9),
  quota_reserved BOOLEAN NOT NULL DEFAULT FALSE,
  quota_usage_date DATE,
  lease_token TEXT,
  lease_expires_at TIMESTAMPTZ,
  provider_request_id TEXT,
  credits_consumed INTEGER NOT NULL DEFAULT 0 CHECK (credits_consumed BETWEEN 0 AND 9),
  phone_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dispatched_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (status IN (
    'queued', 'dispatching', 'awaiting_webhook', 'succeeded',
    'no_match', 'blocked', 'failed', 'cancelled'
  ))
);

CREATE INDEX IF NOT EXISTS idx_apollo_phone_jobs_due
  ON apollo_phone_enrichment_jobs(status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_apollo_phone_jobs_owner_created
  ON apollo_phone_enrichment_jobs(owner_user_id, created_at DESC);

ALTER TABLE apollo_phone_enrichment_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE apollo_phone_enrichment_jobs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS apollo_phone_jobs_read ON apollo_phone_enrichment_jobs;
CREATE POLICY apollo_phone_jobs_read ON apollo_phone_enrichment_jobs
FOR SELECT USING (
  sales_actor_role() IN ('admin', 'system')
  OR owner_user_id = sales_actor_id()
);

DROP POLICY IF EXISTS apollo_phone_jobs_write ON apollo_phone_enrichment_jobs;
CREATE POLICY apollo_phone_jobs_write ON apollo_phone_enrichment_jobs
FOR ALL USING (sales_actor_role() IN ('admin', 'system'))
WITH CHECK (sales_actor_role() IN ('admin', 'system'));
