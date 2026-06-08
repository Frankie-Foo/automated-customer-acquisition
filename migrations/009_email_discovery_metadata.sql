ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS email_source TEXT,
  ADD COLUMN IF NOT EXISTS email_confidence INTEGER,
  ADD COLUMN IF NOT EXISTS email_candidates JSONB NOT NULL DEFAULT '[]'::jsonb;
