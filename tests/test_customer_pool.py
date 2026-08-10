from contextlib import contextmanager
import json
from types import SimpleNamespace

from psycopg._queries import PostgresQuery
from psycopg.adapt import Transformer

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


def test_upsert_keeps_a_public_duplicate_unowned():
    db = FakeDb()
    Repository(db).upsert_contacts(
        [{"linkedin_url": "https://linkedin.com/in/a", "first_name": "A"}],
        owner_user_id=2,
        pool_type="private",
    )

    query, _ = db.conn.calls[-1]
    assert "WHEN contacts.pool_type = 'public' THEN NULL" in query
    assert "pool_type = contacts.pool_type" in query


def test_reply_feedback_freezes_the_assessment_before_it_changes():
    db = FakeDb()
    repo = Repository(db)
    repo.get_contact = lambda _contact_id: {
        "icp_assessment": {"qualified": True, "score": 80, "profile_version": 4}
    }

    repo.update_icp_from_reply(7, {"label": "positive_interested", "positive": True})

    query, params = db.conn.calls[-1]
    saved = json.loads(params[0])
    assert "UPDATE contacts" in query
    assert saved["qualified"] is True
    assert saved["score"] == 90
    assert saved["assessment_before_outcome"] == {
        "qualified": True,
        "score": 80,
        "profile_version": 4,
    }
    assert saved["reply_signal_detail"]["validated_at"].endswith("+00:00")


def test_completing_a_call_task_records_one_phone_interaction():
    class TaskConn(FakeConn):
        def execute(self, query, params=()):
            self.calls.append((query, params))
            if "UPDATE followup_tasks" in query:
                return FakeCursor([{
                    "id": 12,
                    "contact_id": 7,
                    "lead_id": 3,
                    "task_type": "call",
                    "title": "Call Ada",
                    "description": "Record the outcome",
                }])
            return FakeCursor([])

    class TaskDb(FakeDb):
        def __init__(self):
            super().__init__()
            self.conn = TaskConn()

    db = TaskDb()
    task = Repository(db).complete_followup_task(
        12,
        user={"id": 2, "role": "sales"},
        outcome="positive_interested",
    )

    query, params = db.conn.calls[-1]
    assert task["id"] == 12
    assert "INSERT INTO interactions" in query
    assert params[0:3] == (7, 3, 2)
    assert params[5] == "positive_interested"
    assert json.loads(params[7]) == {"task_id": 12}


def test_flywheel_rows_include_recent_phone_outcomes():
    db = FakeDb()
    Repository(db).list_flywheel_contact_rows(window_days=30)

    query, _ = db.conn.calls[-1]
    assert "interaction_type IN ('email_reply', 'phone_call')" in query
    assert "reply_flags.event_count" in query


def test_campaign_metrics_sql_is_valid_for_psycopg_parameter_parsing():
    class PsycopgParsingConn(FakeConn):
        def execute(self, query, params=()):
            parsed = PostgresQuery(Transformer.from_context(None))
            parsed.convert(query, params)
            self.calls.append((query, params))
            return FakeCursor([{"campaign_id": 41}])

    class PsycopgParsingDb(FakeDb):
        def __init__(self):
            super().__init__()
            self.conn = PsycopgParsingConn()

    metrics = Repository(PsycopgParsingDb()).refresh_campaign_metrics(41)

    assert metrics["campaign_id"] == 41


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
