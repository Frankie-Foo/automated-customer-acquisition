CREATE TABLE IF NOT EXISTS llm_gateway_daily_usage (
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  usage_date DATE NOT NULL DEFAULT CURRENT_DATE,
  calls INTEGER NOT NULL DEFAULT 0,
  input_chars INTEGER NOT NULL DEFAULT 0,
  output_chars INTEGER NOT NULL DEFAULT 0,
  CHECK (calls >= 0 AND input_chars >= 0 AND output_chars >= 0),
  PRIMARY KEY (provider, model, usage_date)
);

CREATE TABLE IF NOT EXISTS llm_gateway_cache (
  cache_key TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  operation TEXT NOT NULL,
  response TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_gateway_cache_expires ON llm_gateway_cache(expires_at);
