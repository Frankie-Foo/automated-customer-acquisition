ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS sabcd_stage TEXT NOT NULL DEFAULT 'D';

CREATE INDEX IF NOT EXISTS idx_contacts_sabcd_stage
  ON contacts(sabcd_stage);

UPDATE contacts
SET sabcd_stage = CASE
    WHEN lifecycle_stage IN ('signed', 'maintenance') OR disposition = 'won' THEN 'S'
    WHEN lifecycle_stage IN ('business_plan', 'trial_order', 'agency_agreement', 'store_creation', 'store_visit', 'hq_visit') THEN 'A'
    WHEN lifecycle_stage IN ('replied', 'conversation', 'meeting') OR status = 'replied' THEN 'B'
    WHEN status IN ('sent_1', 'sent_2', 'sent_3') OR sequence_step > 0 THEN 'C'
    ELSE 'D'
  END
WHERE sabcd_stage IS NULL
   OR sabcd_stage = 'D'
   OR sabcd_stage NOT IN ('S', 'A', 'B', 'C', 'D');
