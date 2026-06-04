ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS profile_insights JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_contacts_profile_insights
  ON contacts USING GIN (profile_insights);
