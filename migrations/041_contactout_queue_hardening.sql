UPDATE contactout_accounts
SET daily_limit = 5,
    status = 'disabled',
    updated_at = NOW()
WHERE daily_limit <= 0;

ALTER TABLE contactout_accounts
  DROP CONSTRAINT IF EXISTS contactout_accounts_daily_limit_check;
ALTER TABLE contactout_accounts
  ADD CONSTRAINT contactout_accounts_daily_limit_check CHECK (daily_limit > 0);

ALTER TABLE contactout_enrichment_jobs
  ADD COLUMN IF NOT EXISTS quota_reserved BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS quota_usage_date DATE;

DROP POLICY IF EXISTS contactout_jobs_insert ON contactout_enrichment_jobs;
CREATE POLICY contactout_jobs_insert ON contactout_enrichment_jobs
FOR INSERT
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR (
    sales_actor_role() = 'sales'
    AND owner_user_id = sales_actor_id()
    AND EXISTS (
      SELECT 1
      FROM contacts contact
      WHERE contact.id = contact_id
        AND contact.pool_type = 'private'
        AND contact.owner_user_id = sales_actor_id()
    )
    AND EXISTS (
      SELECT 1
      FROM contactout_accounts account
      WHERE account.id = account_id
        AND account.status = 'active'
        AND account.daily_limit > 0
        AND account.assigned_user_id = sales_actor_id()
    )
  )
);
