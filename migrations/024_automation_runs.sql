CREATE TABLE IF NOT EXISTS automation_runs (
  id BIGSERIAL PRIMARY KEY,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  stage TEXT NOT NULL DEFAULT 'sourcing',
  input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  result JSONB NOT NULL DEFAULT '{}'::jsonb,
  progress_current INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  created_by_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE CASCADE,
  idempotency_key TEXT NOT NULL,
  pause_requested BOOLEAN NOT NULL DEFAULT FALSE,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  UNIQUE(owner_user_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_automation_runs_owner_created
  ON automation_runs(owner_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_automation_runs_status
  ON automation_runs(status, updated_at);
