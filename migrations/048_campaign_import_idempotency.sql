ALTER TABLE campaigns
  ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_campaigns_idempotency_key
  ON campaigns(idempotency_key)
  WHERE idempotency_key IS NOT NULL;
