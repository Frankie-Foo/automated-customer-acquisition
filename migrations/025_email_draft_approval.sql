ALTER TABLE email_drafts
  ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS approved_by_user_id BIGINT REFERENCES sales_users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_email_drafts_approval
  ON email_drafts(contact_id, status, approved_at DESC);
