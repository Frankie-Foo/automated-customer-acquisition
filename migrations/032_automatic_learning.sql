ALTER TABLE outbound_experiments
  ADD COLUMN IF NOT EXISTS winner_variant TEXT,
  ADD COLUMN IF NOT EXISTS winner_selected_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS flywheel_learning_events (
  id BIGSERIAL PRIMARY KEY,
  action_type TEXT NOT NULL,
  scope_type TEXT NOT NULL DEFAULT 'global',
  scope_key TEXT NOT NULL DEFAULT 'global',
  target_id BIGINT,
  before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  reason TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flywheel_learning_scope_created
  ON flywheel_learning_events(scope_type, scope_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_flywheel_learning_target_created
  ON flywheel_learning_events(action_type, target_id, created_at DESC);
