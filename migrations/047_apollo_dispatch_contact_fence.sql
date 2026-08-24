CREATE OR REPLACE FUNCTION guard_apollo_dispatch_contact_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
  target_contact_id BIGINT := COALESCE(NEW.id, OLD.id);
BEGIN
  IF TG_OP = 'DELETE'
     OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
     OR NEW.pool_type IS DISTINCT FROM OLD.pool_type
  THEN
    IF EXISTS (
      SELECT 1
      FROM apollo_phone_enrichment_jobs job
      WHERE job.contact_id = target_contact_id
        AND job.status = 'dispatching'
    ) THEN
      RAISE EXCEPTION 'apollo phone dispatch is in progress for contact %', target_contact_id
        USING ERRCODE = '55000';
    END IF;
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS contacts_apollo_dispatch_fence ON contacts;
CREATE TRIGGER contacts_apollo_dispatch_fence
BEFORE UPDATE OF owner_user_id, pool_type OR DELETE ON contacts
FOR EACH ROW
EXECUTE FUNCTION guard_apollo_dispatch_contact_change();
