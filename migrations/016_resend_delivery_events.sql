ALTER TYPE email_event_type ADD VALUE IF NOT EXISTS 'delivered';
ALTER TYPE email_event_type ADD VALUE IF NOT EXISTS 'delivery_delayed';
ALTER TYPE email_event_type ADD VALUE IF NOT EXISTS 'failed';
ALTER TYPE email_event_type ADD VALUE IF NOT EXISTS 'suppressed';
ALTER TYPE email_event_type ADD VALUE IF NOT EXISTS 'complained';
