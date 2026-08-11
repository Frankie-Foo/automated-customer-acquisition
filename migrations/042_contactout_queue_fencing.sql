DROP POLICY IF EXISTS contactout_jobs_insert ON contactout_enrichment_jobs;
CREATE POLICY contactout_jobs_insert ON contactout_enrichment_jobs
FOR INSERT
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR (
    sales_actor_role() = 'sales'
    AND owner_user_id = sales_actor_id()
    AND operation = 'person_enrich'
    AND status = 'queued'
    AND priority = 0
    AND attempts = 0
    AND max_attempts = 3
    AND quota_units = 1
    AND lease_token IS NULL
    AND lease_expires_at IS NULL
    AND error_code IS NULL
    AND started_at IS NULL
    AND completed_at IS NULL
    AND quota_reserved = FALSE
    AND quota_usage_date IS NULL
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
