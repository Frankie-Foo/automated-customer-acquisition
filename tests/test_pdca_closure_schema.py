from pathlib import Path


def test_pdca_closure_migration_declares_core_tables() -> None:
    sql = Path("migrations/027_unified_pdca_closure.sql").read_text(encoding="utf-8")

    for table in (
        "campaigns",
        "campaign_metrics",
        "leads",
        "interactions",
        "followup_tasks",
        "outreach_messages",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "CREATE OR REPLACE VIEW customer_profiles" in sql
    assert "FROM contacts c" in sql


def test_customer_profiles_view_uses_existing_contact_timestamps() -> None:
    sql = Path("migrations/027_unified_pdca_closure.sql").read_text(encoding="utf-8")

    assert "c.updated_at" not in sql
    assert "COALESCE(c.profile_updated_at, c.enriched_at, c.created_at) AS updated_at" in sql


def test_pdca_workflow_guards_prevent_duplicate_open_work() -> None:
    sql = Path("migrations/028_pdca_workflow_guards.sql").read_text(encoding="utf-8")

    assert "uq_followup_tasks_open_rule" in sql
    assert "WHERE status = 'open' AND trigger_rule IS NOT NULL" in sql
    assert "uq_outreach_messages_draft" in sql


def test_outbound_quality_migration_declares_feedback_and_experiment_storage() -> None:
    sql = Path("migrations/030_outbound_quality_loop.sql").read_text(encoding="utf-8")

    for table in ("icp_profiles", "icp_feedback", "outbound_experiments"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "ADD COLUMN IF NOT EXISTS icp_assessment JSONB" in sql
    assert "ADD COLUMN IF NOT EXISTS quality_review JSONB" in sql
    assert "ADD COLUMN IF NOT EXISTS experiment_id BIGINT" in sql
    assert "ADD COLUMN IF NOT EXISTS positive_replied_count INTEGER" in sql
