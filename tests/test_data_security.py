from pathlib import Path
import re
from types import SimpleNamespace

import sales_automation.db as db_module
from sales_automation.db import Database


ROOT = Path(__file__).resolve().parents[1]


class _Connection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))
        return self

    def fetchone(self):
        return None

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _config():
    return SimpleNamespace(database={
        "host": "db.test",
        "port": 5432,
        "user": "salesbot",
        "password": "not-a-secret",
        "dbname": "salesbot",
    })


def test_database_connection_sets_transaction_local_actor(monkeypatch):
    connection = _Connection()
    driver = SimpleNamespace(connect=lambda **kwargs: connection)
    monkeypatch.setattr(db_module, "_psycopg", lambda: (driver, object()))
    database = Database(_config())
    database.bind_actor({"id": 7, "role": "sales"})

    with database.connect():
        pass

    query, params = next(
        call for call in connection.calls if "set_config('sales.actor_id'" in call[0]
    )
    assert "set_config('sales.actor_id'" in query
    assert "set_config('sales.actor_role'" in query
    assert params == ("7", "sales")


def test_database_connection_drops_superuser_to_runtime_role(monkeypatch):
    class RoleConnection(_Connection):
        def fetchone(self):
            return {"available": 1}

    connection = RoleConnection()
    driver = SimpleNamespace(connect=lambda **kwargs: connection)
    monkeypatch.setattr(db_module, "_psycopg", lambda: (driver, object()))

    with Database(_config()).connect():
        pass

    queries = [query for query, _ in connection.calls]
    assert queries[1] == "SET LOCAL ROLE sales_automation_runtime"
    assert connection.calls[2][1] == ("", "anonymous")


def test_rls_migration_enforces_read_and_write_boundaries():
    sql = (ROOT / "migrations" / "036_tenant_owner_isolation.sql").read_text(encoding="utf-8")

    assert "ALTER TABLE contacts ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE contacts FORCE ROW LEVEL SECURITY" in sql
    assert "NOLOGIN NOSUPERUSER" in sql
    assert "NOBYPASSRLS" in sql
    assert "pool_type = 'public'" in sql
    assert "owner_user_id = sales_actor_id()" in sql
    assert "CREATE TRIGGER contacts_sales_write_guard" in sql
    assert "public contact mutation requires claim or owned sourcing enrichment" in sql


def test_all_runtime_urlopen_calls_use_shared_gate():
    offenders = []
    source_root = ROOT / "src" / "sales_automation"
    for path in source_root.rglob("*.py"):
        if path.name == "http.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "urllib.request.urlopen(" in text or re.search(r"from urllib\.request import .*\burlopen\b", text):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
