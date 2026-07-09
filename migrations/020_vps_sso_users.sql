ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS odoo_user_id INTEGER;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS vps_barcode TEXT;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS department TEXT;
ALTER TABLE sales_users ADD COLUMN IF NOT EXISTS auth_provider TEXT NOT NULL DEFAULT 'local';

CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_users_odoo_user_id
  ON sales_users(odoo_user_id)
  WHERE odoo_user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_users_vps_barcode
  ON sales_users(vps_barcode)
  WHERE vps_barcode IS NOT NULL AND vps_barcode <> '';
