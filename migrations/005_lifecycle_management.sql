ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS outreach_stage TEXT NOT NULL DEFAULT 'not_started',
  ADD COLUMN IF NOT EXISTS lifecycle_stage TEXT NOT NULL DEFAULT 'lead',
  ADD COLUMN IF NOT EXISTS disposition TEXT NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS next_action_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS owner TEXT,
  ADD COLUMN IF NOT EXISTS lost_reason TEXT,
  ADD COLUMN IF NOT EXISTS profile_summary TEXT,
  ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_contacts_lifecycle_stage
  ON contacts(lifecycle_stage);

CREATE INDEX IF NOT EXISTS idx_contacts_disposition_next_action
  ON contacts(disposition, next_action_at);

UPDATE contacts
SET outreach_stage = CASE
    WHEN status = 'unsubscribed' THEN 'opted_out'
    WHEN status = 'bounced' THEN 'bounced'
    WHEN status = 'replied' THEN 'replied'
    WHEN sequence_step >= 3 THEN 'third_touch'
    WHEN sequence_step = 2 THEN 'second_touch'
    WHEN sequence_step = 1 THEN 'first_touch'
    WHEN status = 'queued' THEN 'queued'
    WHEN status = 'enriched' THEN 'ready'
    ELSE outreach_stage
  END,
  lifecycle_stage = CASE
    WHEN status = 'replied' THEN 'replied'
    ELSE lifecycle_stage
  END,
  disposition = CASE
    WHEN status IN ('unsubscribed', 'bounced') THEN 'abandoned'
    WHEN status = 'replied' THEN 'active'
    ELSE disposition
  END
WHERE outreach_stage = 'not_started'
   OR lifecycle_stage = 'lead'
   OR disposition = 'active';
