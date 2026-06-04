CREATE TABLE IF NOT EXISTS lifecycle_activities (
  id BIGSERIAL PRIMARY KEY,
  contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  lifecycle_stage TEXT NOT NULL,
  activity_type TEXT NOT NULL,
  title TEXT,
  content TEXT NOT NULL,
  ai_analysis JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_activities_contact_created
  ON lifecycle_activities(contact_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_lifecycle_activities_stage_type
  ON lifecycle_activities(lifecycle_stage, activity_type);
