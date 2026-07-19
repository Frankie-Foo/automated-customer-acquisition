CREATE UNIQUE INDEX IF NOT EXISTS uq_followup_tasks_open_rule
  ON followup_tasks(contact_id, task_type, trigger_rule)
  WHERE status = 'open' AND trigger_rule IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_outreach_messages_draft
  ON outreach_messages(draft_id)
  WHERE draft_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_followup_tasks_due_open
  ON followup_tasks(due_at, priority)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_interactions_contact_channel
  ON interactions(contact_id, channel, occurred_at DESC);
