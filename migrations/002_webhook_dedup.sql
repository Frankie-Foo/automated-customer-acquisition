ALTER TABLE webhook_events
  ADD COLUMN IF NOT EXISTS external_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_webhook_events_external_id
  ON webhook_events(provider, external_id)
  WHERE external_id IS NOT NULL;

