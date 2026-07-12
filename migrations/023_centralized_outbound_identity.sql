ALTER TABLE sales_users
  ADD COLUMN IF NOT EXISTS sender_alias_localpart TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_users_sender_alias_localpart
  ON sales_users(LOWER(sender_alias_localpart))
  WHERE sender_alias_localpart IS NOT NULL AND sender_alias_localpart <> '';

ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS reply_assignment_pending BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS last_reply_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_contacts_unassigned_replies
  ON contacts(reply_assignment_pending, last_reply_at DESC)
  WHERE reply_assignment_pending = TRUE;
