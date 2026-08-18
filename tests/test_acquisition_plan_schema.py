from pathlib import Path


def test_acquisition_plan_schema_is_durable_and_idempotent():
    sql = Path("migrations/033_acquisition_plans.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS acquisition_plans" in sql
    assert "CREATE TABLE IF NOT EXISTS acquisition_plan_runs" in sql
    assert "UNIQUE(plan_id, run_date)" in sql
    assert "pool_type TEXT NOT NULL DEFAULT 'private'" in sql
    assert "pool_type = 'private' AND owner_user_id IS NOT NULL" in sql


def test_acquisition_plan_scope_migration_preserves_existing_databases():
    sql = Path("migrations/037_acquisition_plan_pool_scope.sql").read_text(encoding="utf-8")

    assert "ALTER COLUMN owner_user_id DROP NOT NULL" in sql
    assert "acquisition_plans_pool_scope_check" in sql


def test_unattended_acquisition_migration_adds_recoverable_items():
    sql = Path("migrations/043_unattended_acquisition_orchestration.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS acquisition_run_items" in sql
    assert "lease_token TEXT" in sql
    assert "lease_expires_at TIMESTAMPTZ" in sql
    assert "retry_wait" in sql
    assert "UNIQUE(run_id, ordinal)" in sql
