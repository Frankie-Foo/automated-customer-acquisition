ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS pdca_subject TEXT;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS pdca_role TEXT;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS pdca_data_scope TEXT;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS pdca_owner_key TEXT;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS pdca_team_key TEXT;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS pdca_owner_keys JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_users_pdca_subject
  ON sales_users(pdca_subject)
  WHERE pdca_subject IS NOT NULL AND pdca_subject <> '';
