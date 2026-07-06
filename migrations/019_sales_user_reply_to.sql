ALTER TABLE sales_users
  ADD COLUMN IF NOT EXISTS reply_to_email TEXT;
