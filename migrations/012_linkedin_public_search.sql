ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS lead_score INTEGER,
  ADD COLUMN IF NOT EXISTS search_task_id BIGINT;

CREATE TABLE IF NOT EXISTS lead_search_tasks (
  id BIGSERIAL PRIMARY KEY,
  created_by_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
  provider TEXT NOT NULL DEFAULT 'google_cse',
  status TEXT NOT NULL DEFAULT 'running',
  requested_limit INTEGER NOT NULL DEFAULT 0,
  query_count INTEGER NOT NULL DEFAULT 0,
  result_count INTEGER NOT NULL DEFAULT 0,
  promoted_count INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS lead_search_results (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES lead_search_tasks(id) ON DELETE CASCADE,
  raw_title TEXT,
  raw_snippet TEXT,
  raw_url TEXT,
  linkedin_url TEXT,
  first_name TEXT,
  last_name TEXT,
  job_title TEXT,
  company_name TEXT,
  company_domain TEXT,
  location TEXT,
  lead_score INTEGER NOT NULL DEFAULT 0,
  email_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
  promoted_contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'parsed',
  failure_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lead_search_tasks_owner_created
  ON lead_search_tasks(owner_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_lead_search_results_task_score
  ON lead_search_results(task_id, lead_score DESC);

CREATE INDEX IF NOT EXISTS idx_contacts_search_task
  ON contacts(search_task_id);
