from pathlib import Path


def test_provider_budget_cache_migration_has_expiring_unique_lookup():
    sql = (Path(__file__).parents[1] / "migrations" / "034_provider_budget_cache.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS provider_lookup_cache" in sql
    assert "PRIMARY KEY (provider, operation, lookup_key)" in sql
    assert "expires_at TIMESTAMPTZ NOT NULL" in sql
