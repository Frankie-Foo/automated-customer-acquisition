ALTER TABLE sales_users
  ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

UPDATE sales_users
SET password_changed_at = COALESCE(password_changed_at, created_at)
WHERE must_change_password = FALSE;
