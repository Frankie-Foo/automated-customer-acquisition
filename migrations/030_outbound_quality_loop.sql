CREATE TABLE IF NOT EXISTS icp_profiles (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'active',
  version INTEGER NOT NULL DEFAULT 1,
  qualified_threshold INTEGER NOT NULL DEFAULT 70,
  review_threshold INTEGER NOT NULL DEFAULT 50,
  criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
  disqualifiers JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_icp_profiles_global_active
  ON icp_profiles(status)
  WHERE owner_user_id IS NULL AND status = 'active';

INSERT INTO icp_profiles(
  name, status, version, qualified_threshold, review_threshold, criteria, disqualifiers
)
VALUES (
  'VERTU channel partner ICP',
  'active',
  1,
  70,
  50,
  '{
    "target_industries": [
      "luxury", "premium retail", "retail", "dealer", "distributor",
      "watch", "jewelry", "jewellery", "fashion", "hospitality",
      "hotel", "automotive", "consumer electronics", "boutique"
    ],
    "target_roles": [
      "owner", "founder", "partner", "ceo", "president", "director",
      "head", "vp", "commercial", "business development", "channel",
      "retail", "procurement"
    ]
  }'::jsonb,
  '[
    "unsubscribed_or_complained", "bounced_email",
    "low_value_role", "missing_company_identity"
  ]'::jsonb
)
ON CONFLICT (name) DO NOTHING;

ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS icp_profile_id BIGINT REFERENCES icp_profiles(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS icp_assessment JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_contacts_icp_tier
  ON contacts((icp_assessment->>'tier'), owner_user_id);

CREATE TABLE IF NOT EXISTS icp_feedback (
  id BIGSERIAL PRIMARY KEY,
  profile_id BIGINT NOT NULL REFERENCES icp_profiles(id) ON DELETE CASCADE,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  reviewer_user_id BIGINT NOT NULL REFERENCES sales_users(id) ON DELETE CASCADE,
  predicted_qualified BOOLEAN NOT NULL,
  expected_qualified BOOLEAN NOT NULL,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(profile_id, contact_id, reviewer_user_id)
);

CREATE INDEX IF NOT EXISTS idx_icp_feedback_profile_created
  ON icp_feedback(profile_id, created_at DESC);

ALTER TABLE email_drafts
  ADD COLUMN IF NOT EXISTS quality_review JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS experiment_id BIGINT,
  ADD COLUMN IF NOT EXISTS experiment_variant TEXT;

CREATE TABLE IF NOT EXISTS outbound_experiments (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  variable_name TEXT NOT NULL,
  owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  campaign_id BIGINT REFERENCES campaigns(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  variants JSONB NOT NULL DEFAULT '[]'::jsonb,
  started_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE email_drafts
  DROP CONSTRAINT IF EXISTS email_drafts_experiment_id_fkey,
  ADD CONSTRAINT email_drafts_experiment_id_fkey
    FOREIGN KEY (experiment_id) REFERENCES outbound_experiments(id) ON DELETE SET NULL;

ALTER TABLE outreach_messages
  ADD COLUMN IF NOT EXISTS quality_review JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS experiment_id BIGINT REFERENCES outbound_experiments(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS experiment_variant TEXT;

CREATE INDEX IF NOT EXISTS idx_outbound_experiments_status
  ON outbound_experiments(status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_email_drafts_experiment
  ON email_drafts(experiment_id, experiment_variant);

CREATE INDEX IF NOT EXISTS idx_outreach_messages_experiment
  ON outreach_messages(experiment_id, experiment_variant, status);

ALTER TABLE campaign_metrics
  ADD COLUMN IF NOT EXISTS positive_replied_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS negative_replied_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS ooo_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS unsubscribe_count INTEGER NOT NULL DEFAULT 0;
