TRUNCATE TABLE llm_gateway_cache;

ALTER TABLE llm_gateway_cache
  ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES sales_users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_llm_gateway_cache_owner
  ON llm_gateway_cache(owner_user_id, expires_at);

ALTER TABLE llm_gateway_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_gateway_cache FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS llm_gateway_cache_read_scope ON llm_gateway_cache;
CREATE POLICY llm_gateway_cache_read_scope ON llm_gateway_cache
FOR SELECT
USING (
  sales_actor_role() IN ('admin', 'system')
  OR (sales_actor_role() = 'sales' AND owner_user_id = sales_actor_id())
);

DROP POLICY IF EXISTS llm_gateway_cache_insert_scope ON llm_gateway_cache;
CREATE POLICY llm_gateway_cache_insert_scope ON llm_gateway_cache
FOR INSERT
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR (sales_actor_role() = 'sales' AND owner_user_id = sales_actor_id())
);

DROP POLICY IF EXISTS llm_gateway_cache_update_scope ON llm_gateway_cache;
CREATE POLICY llm_gateway_cache_update_scope ON llm_gateway_cache
FOR UPDATE
USING (
  sales_actor_role() IN ('admin', 'system')
  OR (sales_actor_role() = 'sales' AND owner_user_id = sales_actor_id())
)
WITH CHECK (
  sales_actor_role() IN ('admin', 'system')
  OR (sales_actor_role() = 'sales' AND owner_user_id = sales_actor_id())
);

DROP POLICY IF EXISTS llm_gateway_cache_delete_scope ON llm_gateway_cache;
CREATE POLICY llm_gateway_cache_delete_scope ON llm_gateway_cache
FOR DELETE
USING (sales_actor_role() IN ('admin', 'system'));
