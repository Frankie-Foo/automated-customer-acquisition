-- A public contact must be claimed explicitly; importing a duplicate cannot grant ownership.
UPDATE contacts
SET owner_user_id = NULL,
    owner = NULL
WHERE pool_type = 'public'
  AND (owner_user_id IS NOT NULL OR owner IS NOT NULL);
