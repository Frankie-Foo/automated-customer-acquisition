ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS source_context JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_contacts_source_context
  ON contacts USING GIN (source_context);
