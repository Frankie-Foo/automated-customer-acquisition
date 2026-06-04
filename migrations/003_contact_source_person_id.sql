ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS source_person_id TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_source_person_id
  ON contacts(source_person_id);

