DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_automation_runtime') THEN
    CREATE ROLE sales_automation_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
  ALTER ROLE sales_automation_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  GRANT sales_automation_runtime TO CURRENT_USER;
END;
$$;

CREATE OR REPLACE FUNCTION sales_actor_role()
RETURNS TEXT
LANGUAGE SQL
STABLE
AS $$
  SELECT COALESCE(NULLIF(current_setting('sales.actor_role', true), ''), 'anonymous')
$$;

CREATE OR REPLACE FUNCTION sales_actor_id()
RETURNS BIGINT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  actor_id TEXT := current_setting('sales.actor_id', true);
BEGIN
  IF actor_id ~ '^[1-9][0-9]*$' THEN
    RETURN actor_id::BIGINT;
  END IF;
  RETURN NULL;
END;
$$;

ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS contacts_read_scope ON contacts;
CREATE POLICY contacts_read_scope ON contacts
FOR SELECT
USING (
  sales_actor_role() IN ('admin', 'system')
  OR (
    sales_actor_role() = 'sales'
    AND (owner_user_id = sales_actor_id() OR pool_type = 'public')
  )
);

DROP POLICY IF EXISTS contacts_insert_scope ON contacts;
CREATE POLICY contacts_insert_scope ON contacts
FOR INSERT
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR (
    sales_actor_role() = 'sales'
    AND (
      (pool_type = 'private' AND owner_user_id = sales_actor_id())
      OR (
        pool_type = 'public'
        AND owner_user_id IS NULL
        AND search_task_id IS NOT NULL
        AND EXISTS (
          SELECT 1
          FROM lead_search_tasks task
          WHERE task.id = search_task_id
            AND task.owner_user_id = sales_actor_id()
        )
      )
    )
  )
);

DROP POLICY IF EXISTS contacts_update_scope ON contacts;
CREATE POLICY contacts_update_scope ON contacts
FOR UPDATE
USING (
  sales_actor_role() IN ('admin', 'system')
  OR (
    sales_actor_role() = 'sales'
    AND (owner_user_id = sales_actor_id() OR pool_type = 'public')
  )
)
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR (
    sales_actor_role() = 'sales'
    AND (
      (pool_type = 'private' AND owner_user_id = sales_actor_id())
      OR (pool_type = 'public' AND owner_user_id IS NULL)
    )
  )
);

DROP POLICY IF EXISTS contacts_delete_scope ON contacts;
CREATE POLICY contacts_delete_scope ON contacts
FOR DELETE
USING (
  sales_actor_role() IN ('admin', 'system')
  OR (
    sales_actor_role() = 'sales'
    AND pool_type = 'private'
    AND owner_user_id = sales_actor_id()
  )
);

CREATE OR REPLACE FUNCTION guard_sales_contact_write()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  actor_id BIGINT := sales_actor_id();
  owns_source_task BOOLEAN;
BEGIN
  IF sales_actor_role() <> 'sales' THEN
    RETURN NEW;
  END IF;

  IF actor_id IS NULL THEN
    RAISE EXCEPTION 'sales actor id is required' USING ERRCODE = '42501';
  END IF;

  IF OLD.pool_type = 'public' THEN
    IF NEW.pool_type = 'private'
       AND NEW.owner_user_id = actor_id
       AND NEW.assignment_source = 'manual_claim'
       AND NEW.assigned_at IS NOT NULL
       AND NEW.pool_expires_at IS NOT NULL
       AND NEW.returned_to_public_at IS NULL
       AND NEW.claim_count = OLD.claim_count + 1
       AND (to_jsonb(NEW) - ARRAY[
         'pool_type', 'owner_user_id', 'owner', 'assignment_source', 'assigned_at',
         'pool_expires_at', 'returned_to_public_at', 'claim_count'
       ]::TEXT[]) IS NOT DISTINCT FROM (to_jsonb(OLD) - ARRAY[
         'pool_type', 'owner_user_id', 'owner', 'assignment_source', 'assigned_at',
         'pool_expires_at', 'returned_to_public_at', 'claim_count'
       ]::TEXT[])
    THEN
      RETURN NEW;
    END IF;

    SELECT EXISTS (
      SELECT 1
      FROM lead_search_tasks task
      WHERE task.id = OLD.search_task_id
        AND task.owner_user_id = actor_id
    ) INTO owns_source_task;

    IF owns_source_task
       AND NEW.pool_type = 'private'
       AND NEW.owner_user_id = actor_id
       AND NEW.assignment_source = 'direct_import'
       AND NEW.assigned_at IS NOT NULL
       AND NEW.pool_expires_at IS NOT NULL
       AND NEW.returned_to_public_at IS NULL
       AND NEW.claim_count = OLD.claim_count
       AND (to_jsonb(NEW) - ARRAY[
         'pool_type', 'owner_user_id', 'owner', 'assignment_source', 'assigned_at',
         'pool_expires_at', 'returned_to_public_at'
       ]::TEXT[]) IS NOT DISTINCT FROM (to_jsonb(OLD) - ARRAY[
         'pool_type', 'owner_user_id', 'owner', 'assignment_source', 'assigned_at',
         'pool_expires_at', 'returned_to_public_at'
       ]::TEXT[])
    THEN
      RETURN NEW;
    END IF;

    IF owns_source_task
       AND NEW.pool_type = 'public'
       AND NEW.owner_user_id IS NULL
       AND (to_jsonb(NEW) - ARRAY[
         'source_person_id', 'lead_score', 'identity_confidence', 'identity_status',
         'identity_evidence', 'email_candidates', 'email', 'phone', 'phone_candidates',
         'source_context', 'email_status', 'status', 'enrich_error', 'first_name',
         'last_name', 'job_title', 'company_name', 'company_domain', 'industry', 'location'
       ]::TEXT[]) IS NOT DISTINCT FROM (to_jsonb(OLD) - ARRAY[
         'source_person_id', 'lead_score', 'identity_confidence', 'identity_status',
         'identity_evidence', 'email_candidates', 'email', 'phone', 'phone_candidates',
         'source_context', 'email_status', 'status', 'enrich_error', 'first_name',
         'last_name', 'job_title', 'company_name', 'company_domain', 'industry', 'location'
       ]::TEXT[])
    THEN
      RETURN NEW;
    END IF;

    RAISE EXCEPTION 'public contact mutation requires claim or owned sourcing enrichment'
      USING ERRCODE = '42501';
  END IF;

  IF OLD.owner_user_id IS DISTINCT FROM actor_id THEN
    RAISE EXCEPTION 'contact is owned by another sales user' USING ERRCODE = '42501';
  END IF;

  IF NEW.pool_type = 'public' THEN
    IF NEW.owner_user_id IS NULL
       AND NEW.assigned_at IS NULL
       AND NEW.pool_expires_at IS NULL
       AND NEW.returned_to_public_at IS NOT NULL
       AND NEW.claim_count = OLD.claim_count
       AND NEW.disposition = (CASE WHEN OLD.disposition = 'won' THEN 'won' ELSE 'active' END)
       AND (to_jsonb(NEW) - ARRAY[
         'pool_type', 'owner_user_id', 'owner', 'assignment_source', 'assigned_at',
         'pool_expires_at', 'returned_to_public_at', 'disposition'
       ]::TEXT[]) IS NOT DISTINCT FROM (to_jsonb(OLD) - ARRAY[
         'pool_type', 'owner_user_id', 'owner', 'assignment_source', 'assigned_at',
         'pool_expires_at', 'returned_to_public_at', 'disposition'
       ]::TEXT[])
    THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid public-pool return' USING ERRCODE = '42501';
  END IF;

  IF NEW.pool_type <> 'private' OR NEW.owner_user_id IS DISTINCT FROM actor_id THEN
    RAISE EXCEPTION 'sales user cannot transfer contact ownership' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS contacts_sales_write_guard ON contacts;
CREATE TRIGGER contacts_sales_write_guard
BEFORE UPDATE ON contacts
FOR EACH ROW
EXECUTE FUNCTION guard_sales_contact_write();

CREATE OR REPLACE FUNCTION sales_can_read_contact(target_contact_id BIGINT)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
AS $$
  SELECT sales_actor_role() IN ('admin', 'system') OR EXISTS (
    SELECT 1 FROM contacts WHERE id = target_contact_id
  )
$$;

CREATE OR REPLACE FUNCTION sales_can_write_contact(target_contact_id BIGINT)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
AS $$
  SELECT sales_actor_role() IN ('admin', 'system') OR EXISTS (
    SELECT 1
    FROM contacts
    WHERE id = target_contact_id
      AND pool_type = 'private'
      AND owner_user_id = sales_actor_id()
  )
$$;

DO $$
DECLARE
  table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'email_events', 'lifecycle_activities', 'contact_research', 'email_drafts',
    'outbound_send_attempts', 'interactions', 'followup_tasks', 'outreach_messages',
    'icp_feedback'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS contact_read_scope ON %I', table_name);
    EXECUTE format(
      'CREATE POLICY contact_read_scope ON %I FOR SELECT USING (sales_can_read_contact(contact_id))',
      table_name
    );
    EXECUTE format('DROP POLICY IF EXISTS contact_write_scope ON %I', table_name);
    EXECUTE format(
      'CREATE POLICY contact_write_scope ON %I FOR ALL USING (sales_can_write_contact(contact_id)) WITH CHECK (sales_can_write_contact(contact_id))',
      table_name
    );
  END LOOP;
END;
$$;

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS leads_scope ON leads;
CREATE POLICY leads_scope ON leads
FOR ALL
USING (
  sales_actor_role() IN ('admin', 'system')
  OR owner_user_id = sales_actor_id()
  OR sales_can_read_contact(contact_id)
)
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR (
    owner_user_id = sales_actor_id()
    AND (contact_id IS NULL OR sales_can_write_contact(contact_id))
  )
);

ALTER VIEW customer_profiles SET (security_invoker = true);

GRANT USAGE ON SCHEMA public TO sales_automation_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sales_automation_runtime;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO sales_automation_runtime;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO sales_automation_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sales_automation_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO sales_automation_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO sales_automation_runtime;
