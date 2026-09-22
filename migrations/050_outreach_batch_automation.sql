ALTER TABLE campaigns
  ADD COLUMN IF NOT EXISTS automation_status TEXT NOT NULL DEFAULT 'idle',
  ADD COLUMN IF NOT EXISTS automation_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS automation_error TEXT,
  ADD COLUMN IF NOT EXISTS automation_started_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS automation_completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS automation_updated_at TIMESTAMPTZ;

ALTER TABLE campaigns
  DROP CONSTRAINT IF EXISTS campaigns_automation_status_check;

ALTER TABLE campaigns
  ADD CONSTRAINT campaigns_automation_status_check CHECK (
    automation_status IN ('idle', 'running', 'paused', 'completed', 'failed')
  );

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS automation_status TEXT,
  ADD COLUMN IF NOT EXISTS automation_reason TEXT,
  ADD COLUMN IF NOT EXISTS automation_attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS automation_updated_at TIMESTAMPTZ;

ALTER TABLE leads
  DROP CONSTRAINT IF EXISTS leads_automation_status_check;

ALTER TABLE leads
  ADD CONSTRAINT leads_automation_status_check CHECK (
    automation_status IS NULL OR automation_status IN (
      'pending', 'researching', 'ready', 'sending', 'sent', 'held', 'retry', 'failed'
    )
  );

CREATE INDEX IF NOT EXISTS idx_campaigns_outreach_automation
  ON campaigns(automation_status, updated_at)
  WHERE channel = 'outreach_batch';

CREATE INDEX IF NOT EXISTS idx_leads_outreach_automation
  ON leads(campaign_id, automation_status, source_row, id)
  WHERE source_type = 'outreach_batch';
