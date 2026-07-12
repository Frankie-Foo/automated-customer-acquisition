CREATE TABLE IF NOT EXISTS app_settings (
  setting_key TEXT PRIMARY KEY,
  setting_value JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_by_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO app_settings(setting_key, setting_value)
VALUES ('customer_pool.region_assignments', '[]'::jsonb)
ON CONFLICT (setting_key) DO NOTHING;
