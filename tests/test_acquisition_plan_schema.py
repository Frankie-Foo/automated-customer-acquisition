from pathlib import Path


def test_acquisition_plan_schema_is_durable_and_idempotent():
    sql = Path("migrations/033_acquisition_plans.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS acquisition_plans" in sql
    assert "CREATE TABLE IF NOT EXISTS acquisition_plan_runs" in sql
    assert "UNIQUE(plan_id, run_date)" in sql
    assert "owner_user_id BIGINT NOT NULL" in sql
