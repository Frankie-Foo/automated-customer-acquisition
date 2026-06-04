ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS social_profiles JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS social_enriched_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS social_error TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_social_profiles
  ON contacts USING GIN (social_profiles);
