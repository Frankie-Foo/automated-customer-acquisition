ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS pool_type TEXT NOT NULL DEFAULT 'private',
  ADD COLUMN IF NOT EXISTS assignment_source TEXT,
  ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS pool_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_stage_changed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS returned_to_public_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS claim_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS customer_profile_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE contacts
SET pool_type = CASE WHEN owner_user_id IS NULL THEN 'public' ELSE 'private' END,
    assigned_at = CASE WHEN owner_user_id IS NULL THEN assigned_at ELSE COALESCE(assigned_at, created_at) END,
    pool_expires_at = CASE
      WHEN owner_user_id IS NULL THEN NULL
      ELSE COALESCE(pool_expires_at, COALESCE(assigned_at, created_at) + INTERVAL '60 days')
    END,
    last_stage_changed_at = COALESCE(last_stage_changed_at, created_at)
WHERE pool_type NOT IN ('public', 'private')
   OR assigned_at IS NULL
   OR last_stage_changed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_pool_type_created
  ON contacts(pool_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contacts_owner_pool_expiry
  ON contacts(owner_user_id, pool_type, pool_expires_at);

CREATE INDEX IF NOT EXISTS idx_contacts_pool_region
  ON contacts(pool_type, location);
