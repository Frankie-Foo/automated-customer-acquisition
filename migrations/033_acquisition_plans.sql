CREATE TABLE IF NOT EXISTS acquisition_plans (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  regions JSONB NOT NULL DEFAULT '[]'::jsonb,
  industries JSONB NOT NULL DEFAULT '[]'::jsonb,
  company_types JSONB NOT NULL DEFAULT '[]'::jsonb,
  role_terms JSONB NOT NULL DEFAULT '[]'::jsonb,
  owner_user_id BIGINT NOT NULL REFERENCES sales_users(id) ON DELETE RESTRICT,
  daily_lead_limit INTEGER NOT NULL DEFAULT 20 CHECK (daily_lead_limit BETWEEN 1 AND 1000),
  combinations_per_run INTEGER NOT NULL DEFAULT 3 CHECK (combinations_per_run BETWEEN 1 AND 50),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'archived')),
  next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  cursor_position INTEGER NOT NULL DEFAULT 0,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_acquisition_plans_due
  ON acquisition_plans(status, next_run_at);

CREATE TABLE IF NOT EXISTS acquisition_plan_runs (
  id BIGSERIAL PRIMARY KEY,
  plan_id BIGINT NOT NULL REFERENCES acquisition_plans(id) ON DELETE CASCADE,
  run_date DATE NOT NULL DEFAULT CURRENT_DATE,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
  combinations JSONB NOT NULL DEFAULT '[]'::jsonb,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  UNIQUE(plan_id, run_date)
);

CREATE INDEX IF NOT EXISTS idx_acquisition_plan_runs_plan_date
  ON acquisition_plan_runs(plan_id, run_date DESC);
