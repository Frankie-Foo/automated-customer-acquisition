CREATE TABLE IF NOT EXISTS system_loop_runs (
  id BIGSERIAL PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'running' CHECK (
    status IN ('running', 'completed', 'completed_with_errors', 'failed')
  ),
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  errors JSONB NOT NULL DEFAULT '[]'::jsonb,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_loop_runs_started
  ON system_loop_runs(started_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON system_loop_runs TO sales_automation_runtime;
GRANT USAGE, SELECT ON SEQUENCE system_loop_runs_id_seq TO sales_automation_runtime;
