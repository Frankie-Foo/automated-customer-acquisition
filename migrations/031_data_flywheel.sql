CREATE TABLE IF NOT EXISTS flywheel_strategy_snapshots (
  id BIGSERIAL PRIMARY KEY,
  scope_type TEXT NOT NULL DEFAULT 'global',
  scope_key TEXT NOT NULL DEFAULT 'global',
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active',
  window_start TIMESTAMPTZ NOT NULL,
  window_end TIMESTAMPTZ NOT NULL,
  sample_size INTEGER NOT NULL DEFAULT 0,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  rules JSONB NOT NULL DEFAULT '{}'::jsonb,
  guidance JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_flywheel_active_scope
  ON flywheel_strategy_snapshots(scope_type, scope_key)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_flywheel_scope_updated
  ON flywheel_strategy_snapshots(scope_type, scope_key, updated_at DESC);
