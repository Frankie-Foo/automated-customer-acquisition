ALTER TABLE acquisition_plan_runs
  DROP CONSTRAINT IF EXISTS acquisition_plan_runs_status_check;

ALTER TABLE acquisition_plan_runs
  ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'sourcing',
  ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3,
  ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS lease_token TEXT,
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE acquisition_plan_runs ALTER COLUMN status SET DEFAULT 'queued';

UPDATE acquisition_plan_runs
SET status = 'retry_wait', next_attempt_at = NOW(), updated_at = NOW()
WHERE status = 'running' AND completed_at IS NULL;

ALTER TABLE acquisition_plan_runs
  ADD CONSTRAINT acquisition_plan_runs_status_check
  CHECK (status IN (
    'queued', 'running', 'completed', 'completed_partial',
    'retry_wait', 'blocked', 'failed', 'cancelled'
  ));

ALTER TABLE acquisition_plan_runs
  DROP CONSTRAINT IF EXISTS acquisition_plan_runs_attempts_check;
ALTER TABLE acquisition_plan_runs
  ADD CONSTRAINT acquisition_plan_runs_attempts_check
  CHECK (attempts >= 0 AND max_attempts BETWEEN 1 AND 10);

CREATE TABLE IF NOT EXISTS acquisition_run_items (
  id BIGSERIAL PRIMARY KEY,
  run_id BIGINT NOT NULL REFERENCES acquisition_plan_runs(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  stage TEXT NOT NULL DEFAULT 'sourcing',
  criteria JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued' CHECK (
    status IN ('queued', 'running', 'completed', 'retry_wait', 'blocked', 'failed', 'cancelled')
  ),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
  next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  lease_token TEXT,
  lease_expires_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  error TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(run_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_acquisition_run_items_claim
  ON acquisition_run_items(status, next_attempt_at, id);

CREATE INDEX IF NOT EXISTS idx_acquisition_plan_runs_resume
  ON acquisition_plan_runs(status, next_attempt_at, id);

GRANT SELECT, INSERT, UPDATE, DELETE ON acquisition_run_items TO sales_automation_runtime;
GRANT USAGE, SELECT ON SEQUENCE acquisition_run_items_id_seq TO sales_automation_runtime;
