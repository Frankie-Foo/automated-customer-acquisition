from contextlib import contextmanager
from types import SimpleNamespace

from sales_automation.db import Repository


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if "RETURNING (xmax = 0) AS inserted" in query:
            return FakeCursor([{"inserted": True}])
        if "UPDATE contacts" in query and "manual_claim" in query:
            return FakeCursor([{"id": 7, "pool_type": "private", "profile_insights": {}}])
        return FakeCursor([])


class FakeDb:
    def __init__(self):
        self.config = SimpleNamespace(raw={"customer_pool": {"private_pool_days": 60}})
        self.conn = FakeConn()

    @contextmanager
    def connect(self):
        yield self.conn


def test_upsert_contacts_without_owner_defaults_to_public_pool():
    db = FakeDb()
    inserted, skipped = Repository(db).upsert_contacts([
        {"linkedin_url": "https://linkedin.com/in/a", "first_name": "A"}
    ])

    assert (inserted, skipped) == (1, 0)
    _, params = db.conn.calls[-1]
    assert params["owner_user_id"] is None
    assert params["pool_type"] == "public"
    assert params["assignment_source"] == "automated_sourcing"


def test_sales_user_can_view_public_pool_contact():
    db = FakeDb()
    Repository(db).get_contact_for_user(7, {"id": 2, "role": "sales"})
    query, params = db.conn.calls[-1]

    assert "owner_user_id = %s OR pool_type = 'public'" in query
    assert params == (7, 2)


def test_private_operations_query_only_private_pool():
    db = FakeDb()
    Repository(db).queue_contacts(25, user={"id": 2, "role": "sales"})
    query, params = db.conn.calls[-1]

    assert "pool_type = 'private'" in query
    assert "contacts.owner_user_id = %s" in query
    assert params == (2, 25)


def test_claim_public_contact_moves_to_private_pool():
    db = FakeDb()
    row = Repository(db).claim_public_contact(7, {"id": 2, "username": "april", "display_name": "April"})
    query, params = db.conn.calls[-1]

    assert row["pool_type"] == "private"
    assert "pool_type = 'private'" in query
    assert "assignment_source = 'manual_claim'" in query
    assert params == (2, "April", 60, 7)
