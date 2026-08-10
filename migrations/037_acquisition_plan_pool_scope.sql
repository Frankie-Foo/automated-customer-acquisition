ALTER TABLE acquisition_plans
  ADD COLUMN IF NOT EXISTS pool_type TEXT NOT NULL DEFAULT 'private';

ALTER TABLE acquisition_plans
  ALTER COLUMN owner_user_id DROP NOT NULL;

ALTER TABLE acquisition_plans
  DROP CONSTRAINT IF EXISTS acquisition_plans_pool_type_check,
  DROP CONSTRAINT IF EXISTS acquisition_plans_pool_scope_check;

ALTER TABLE acquisition_plans
  ADD CONSTRAINT acquisition_plans_pool_type_check
    CHECK (pool_type IN ('private', 'public')),
  ADD CONSTRAINT acquisition_plans_pool_scope_check
    CHECK (
      (pool_type = 'private' AND owner_user_id IS NOT NULL)
      OR (pool_type = 'public' AND owner_user_id IS NULL)
    );
