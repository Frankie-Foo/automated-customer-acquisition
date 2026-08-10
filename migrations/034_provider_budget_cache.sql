CREATE TABLE IF NOT EXISTS provider_lookup_cache (
  provider TEXT NOT NULL,
  operation TEXT NOT NULL,
  lookup_key TEXT NOT NULL,
  status TEXT NOT NULL,
  response JSONB NOT NULL DEFAULT '[]'::jsonb,
  credits_reserved INTEGER NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (provider, operation, lookup_key)
);

CREATE INDEX IF NOT EXISTS idx_provider_lookup_cache_expires
  ON provider_lookup_cache(expires_at);
