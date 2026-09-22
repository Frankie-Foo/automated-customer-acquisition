"""Offline batch regressions and opt-in, temporary-table PostgreSQL contracts."""
import json
import os
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from sales_automation import web
from sales_automation.db import Repository
from sales_automation.services.outreach import PersonalizedEmailService
from sales_automation.services.outreach_batches import OutreachBatchService, _actor, summarize


SALES = {"id": 2, "role": "sales"}
OTHER_SALES = {"id": 3, "role": "sales"}
QA_PG_DSN = os.getenv("SALESBOT_QA_PG_DSN")


@pytest.mark.parametrize("user", [
    None, {}, {"role": "sales"}, {"id": 0, "role": "admin"},
    {"id": 2}, {"id": 2, "role": "viewer"}, {"id": 2, "role": "system"},
])
def test_actor_requires_identity_and_supported_role(user):
    with pytest.raises(PermissionError, match="Authentication required"):
        _actor(user)


@pytest.mark.parametrize("user, expected", [
    (SALES, (2, False)), ({"id": "3", "role": "sales"}, (3, False)),
    ({"id": 1, "role": "admin"}, (1, True)),
])
def test_actor_returns_identity_and_explicit_admin_flag(user, expected):
    assert _actor(user) == expected


@pytest.fixture
def mock_repo():
    return SimpleNamespace(db=MagicMock())


@pytest.mark.parametrize("operation", ["create", "list", "detail"])
def test_service_requires_authentication_before_database_access(mock_repo, operation):
    service = OutreachBatchService(mock_repo)
    with pytest.raises(PermissionError, match="Authentication required"):
        if operation == "create":
            service.create(user=None, name="Batch", contact_ids=[10])
        elif operation == "list":
            service.list(user=None)
        else:
            service.detail(1, user=None)
    mock_repo.db.connect.assert_not_called()


@pytest.mark.parametrize("name", [None, "", " \t\n", "x" * 201])
def test_create_rejects_invalid_names_before_database_access(mock_repo, name):
    with pytest.raises(ValueError, match="Batch name must contain 1-200 characters"):
        OutreachBatchService(mock_repo).create(user=SALES, name=name, contact_ids=[10])
    mock_repo.db.connect.assert_not_called()


@pytest.mark.parametrize("contact_ids", [None, [], (), (10,), "10", {"id": 10}, list(range(1, 1002))])
def test_create_requires_a_bounded_contact_list(mock_repo, contact_ids):
    with pytest.raises(ValueError, match="Select 1-1000 contacts"):
        OutreachBatchService(mock_repo).create(user=SALES, name="Batch", contact_ids=contact_ids)
    mock_repo.db.connect.assert_not_called()


@pytest.mark.parametrize("contact_id", [0, -1, True, False, "10", 10.0, None, {}, []])
def test_create_rejects_non_positive_or_non_integer_contact_ids(mock_repo, contact_id):
    with pytest.raises(ValueError, match="Contact IDs must be positive integers"):
        OutreachBatchService(mock_repo).create(user=SALES, name="Batch", contact_ids=[10, contact_id])
    mock_repo.db.connect.assert_not_called()


def test_create_accepts_maximum_name_and_roster_size(mock_repo):
    conn = mock_repo.db.connect.return_value.__enter__.return_value
    ids = list(range(1, 1001))
    conn.execute.return_value.fetchall.return_value = [
        {"id": contact_id, "owner_user_id": 2, "pool_type": "private"} for contact_id in ids
    ]
    conn.execute.return_value.fetchone.return_value = {"id": 7}

    result = OutreachBatchService(mock_repo).create(user=SALES, name="x" * 200, contact_ids=ids)

    assert result == {"batch": {"id": 7}, "total": 1000}
    assert conn.execute.call_args_list[0].args[1] == (ids,)
    assert len(conn.execute.call_args_list) == 1002


def test_summary_counts_contacts_once_but_sums_messages():
    rows = [
        {"researched_at": "2026-01-01", "latest_subject": "First", "sent_count": 3,
         "replied_count": 2, "failed_count": 2, "eligibility_reason": None},
        {"latest_subject": "Second", "sent_count": 2, "replied_count": 0,
         "failed_count": 1, "eligibility_reason": "email_unverified"},
        {"sent_count": None, "replied_count": None, "failed_count": None},
    ]
    assert summarize(rows) == {
        "total_count": 3, "profiled_contacts": 1, "drafted_contacts": 2,
        "sent_contacts": 2, "sent_messages": 5, "replied_contacts": 1,
        "failed_messages": 3, "blocked_contacts": 1,
    }
    assert summarize([]) == dict.fromkeys(summarize(rows), 0)


@pytest.mark.parametrize("actor", [None, SALES])
def test_drafts_use_resolved_sender_for_background_and_logged_in_user(monkeypatch, actor):
    from sales_automation.services import outreach

    contact = {"id": 10, "owner_user_id": 2, "company_name": "Acme Retail"}
    repo = Mock()
    repo.get_contact.return_value = repo.get_private_contact_for_user.return_value = contact
    repo.get_user_by_id.return_value = SALES
    monkeypatch.setattr(outreach, "_signature_profile", lambda *_: {})
    monkeypatch.setattr(outreach, "OutboundQualityService", Mock())
    service = PersonalizedEmailService(SimpleNamespace(raw={}, sender={}), repo)
    service._save_draft = Mock()
    service._ai_draft = Mock(return_value={"subject": "Test", "body": ""})

    for mode in ("custom", "ai"):
        service.draft(10, user=actor, mode=mode)
        assert service._save_draft.call_args.kwargs["user"] == SALES


def test_draft_rejects_foreign_campaign_before_generation():
    repo = MagicMock()
    repo.get_private_contact_for_user.return_value = {"id": 10, "company_name": "Acme Retail"}
    conn = repo.db.connect.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None
    service = PersonalizedEmailService(SimpleNamespace(raw={}, sender={}), repo)
    service._ai_draft = Mock()
    with pytest.raises(PermissionError, match="not in this salesperson's batch"):
        service.draft(10, user=SALES, campaign_id=7)
    assert conn.execute.call_args.args[1] == (7, 10, 2)
    service._ai_draft.assert_not_called()
    repo.save_email_draft.assert_not_called()


@pytest.mark.parametrize("limit, offset, expected_ids", [
    ("2", "1", [1, 2]), (0, -5, [0]), (1000, 0, list(range(100))), (25, 105, []),
])
def test_detail_bounds_pagination_without_changing_totals(mock_repo, limit, offset, expected_ids):
    conn = mock_repo.db.connect.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = {"id": 7}
    conn.execute.return_value.fetchall.return_value = [{"id": i} for i in range(105)]

    result = OutreachBatchService(mock_repo).detail(7, user=SALES, limit=limit, offset=offset)

    assert [row["id"] for row in result["contacts"]] == expected_ids
    assert result["total"] == result["summary"]["total_count"] == 105


@pytest.fixture
def batch_api(monkeypatch, mock_repo):
    mock_repo.get_session_user = Mock(side_effect=lambda token: SALES if token == "sales-token" else None)
    monkeypatch.setattr(web, "check_database", lambda repo: {"ok": True})
    handler = web.make_handler(SimpleNamespace(raw={"app": {}}), mock_repo)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(path, token="sales-token"):
        headers = {"Cookie": f"salesbot_session={token}"} if token else {}
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, json.load(response)

    try:
        yield get, mock_repo
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("path", ["/api/outreach-batches", "/api/outreach-batches/7"])
@pytest.mark.parametrize("token", [None, "expired-token"])
def test_get_routes_require_authenticated_session(batch_api, path, token):
    get, repo = batch_api
    assert get(path, token) == (401, {"ok": False, "error": "unauthorized"})
    repo.db.connect.assert_not_called()


def test_get_routes_pass_session_scope_and_detail_options(batch_api, monkeypatch):
    get, repo = batch_api
    service = Mock(spec=OutreachBatchService)
    service.list.return_value = [{"id": 7, "name": "Owned"}]
    service.detail.return_value = {"batch": {"id": 7}, "contacts": [], "total": 0, "summary": {}}
    factory = Mock(return_value=service)
    monkeypatch.setattr(web, "OutreachBatchService", factory)

    assert get("/api/outreach-batches?user_id=3&role=admin") == (
        200, {"ok": True, "data": {"batches": service.list.return_value}},
    )
    service.list.assert_called_once_with(user=SALES)
    assert get("/api/outreach-batches/7?limit=2&offset=4&search=ACME+Retail&user_id=3&role=admin") == (
        200, {"ok": True, "data": service.detail.return_value},
    )
    service.detail.assert_called_once_with(7, user=SALES, limit="2", offset="4", search="ACME Retail")
    get("/api/outreach-batches/7")
    service.detail.assert_called_with(7, user=SALES, limit="25", offset="0", search="")
    factory.assert_called_with(repo)


@pytest.mark.parametrize("suffix", ["7?limit=bad", "7?offset=bad", "7?limit=1.5", "not-a-batch"])
def test_get_detail_invalid_pagination_or_id_returns_400(batch_api, suffix):
    get, repo = batch_api
    status, payload = get(f"/api/outreach-batches/{suffix}")
    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]
    repo.db.connect.assert_not_called()


def test_get_detail_unknown_or_inaccessible_batch_returns_403(batch_api):
    get, repo = batch_api
    conn = repo.db.connect.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None

    assert get("/api/outreach-batches/999") == (
        403, {"ok": False, "error": "Batch not found or not accessible"},
    )
    assert conn.execute.call_args.args[1] == (999, False, 2)
    assert conn.execute.call_count == 1


def test_record_message_upsert_sql_preserves_existing_attribution_and_evidence(mock_repo):
    conn = mock_repo.db.connect.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = {"id": 202, "campaign_id": 22}

    Repository(mock_repo.db).record_outreach_message(
        contact_id=10, user_id=2, draft_id=100, channel="email", body="Sent body", status="sent",
    )

    lookup, upsert = conn.execute.call_args_list
    assert lookup.args[1] == (10, None, None)
    assert upsert.args[1][1:3] == (202, 22)
    assert json.loads(upsert.args[1][11]) == []
    sql = " ".join(upsert.args[0].split())
    assert "ON CONFLICT (draft_id) WHERE draft_id IS NOT NULL" in sql
    assert "lead_id = COALESCE(outreach_messages.lead_id, EXCLUDED.lead_id)" in sql
    assert "campaign_id = COALESCE(outreach_messages.campaign_id, EXCLUDED.campaign_id)" in sql
    assert (
        "personalization_evidence = CASE WHEN EXCLUDED.personalization_evidence = '[]'::jsonb "
        "THEN outreach_messages.personalization_evidence ELSE EXCLUDED.personalization_evidence END"
    ) in sql


@pytest.fixture
def pg_batches():
    if not QA_PG_DSN:
        pytest.skip("isolated QA PostgreSQL not configured")
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(QA_PG_DSN, row_factory=dict_row, connect_timeout=5) as conn:
        try:
            # Never resolve an omitted fixture table to a persistent application table.
            conn.execute("SET LOCAL search_path TO pg_temp")
            conn.execute("""
                CREATE TEMP TABLE sales_users (id bigint PRIMARY KEY, username text, display_name text);
                CREATE TEMP TABLE contacts (
                    id bigint PRIMARY KEY, owner_user_id bigint, pool_type text DEFAULT 'private',
                    first_name text, last_name text, company_name text, company_domain text, job_title text,
                    email text, email_status text DEFAULT 'valid', phone text, linkedin_url text,
                    location text, industry text, source text, source_context jsonb DEFAULT '{}',
                    status text DEFAULT 'new', lifecycle_stage text DEFAULT 'new', profile_summary text
                );
                CREATE TEMP TABLE campaigns (
                    id bigserial PRIMARY KEY, name text, channel text, region text, owner_user_id bigint,
                    idempotency_key text, metadata jsonb DEFAULT '{}', created_at timestamptz DEFAULT NOW()
                );
                CREATE UNIQUE INDEX qa_campaign_key ON campaigns(idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE TEMP TABLE leads (
                    id bigserial PRIMARY KEY, external_id text, source_type text, source_ref text,
                    source_row integer, campaign_id bigint, contact_id bigint, owner_user_id bigint,
                    raw_data jsonb DEFAULT '{}', normalized_email text, status text,
                    updated_at timestamptz DEFAULT NOW(), UNIQUE(source_type, external_id)
                );
                CREATE TEMP TABLE contact_research (
                    contact_id bigint PRIMARY KEY, summary text, sources jsonb, researched_at timestamptz
                );
                CREATE TEMP TABLE followup_tasks (
                    id bigserial PRIMARY KEY, contact_id bigint, assigned_user_id bigint, title text,
                    due_at timestamptz, status text DEFAULT 'open'
                );
                CREATE TEMP TABLE email_drafts (
                    id bigserial PRIMARY KEY, contact_id bigint, user_id bigint, status text DEFAULT 'draft',
                    sequence_step integer, mode text, subject text, body text, research_snapshot jsonb,
                    quality_review jsonb, experiment_id bigint, experiment_variant text,
                    created_at timestamptz DEFAULT NOW(), sent_at timestamptz, approved_at timestamptz,
                    approved_by_user_id bigint
                );
                CREATE TEMP TABLE outreach_messages (
                    id bigserial PRIMARY KEY, contact_id bigint, lead_id bigint, campaign_id bigint,
                    user_id bigint, draft_id bigint, channel text DEFAULT 'email', sequence_step integer DEFAULT 1,
                    subject text, body text DEFAULT '', language text, ai_model text,
                    personalization_evidence jsonb DEFAULT '[]', status text DEFAULT 'draft',
                    provider text, provider_message_id text, error text, quality_review jsonb DEFAULT '{}',
                    experiment_id bigint, experiment_variant text, metadata jsonb DEFAULT '{}',
                    sent_at timestamptz, replied_at timestamptz, opened_at timestamptz,
                    created_at timestamptz DEFAULT NOW(), updated_at timestamptz DEFAULT NOW()
                );
                CREATE UNIQUE INDEX qa_message_draft ON outreach_messages(draft_id) WHERE draft_id IS NOT NULL;
                CREATE TEMP TABLE email_events (
                    contact_id bigint, sequence_step integer, event_type text, email_subject text,
                    message_id text, occurred_at timestamptz, metadata jsonb DEFAULT '{}'
                );
                CREATE TEMP TABLE interactions (
                    contact_id bigint, interaction_type text, occurred_at timestamptz, metadata jsonb DEFAULT '{}'
                );
                INSERT INTO sales_users VALUES (1, 'admin', 'Admin'), (2, 'sales', 'Owner'), (3, 'other', 'Other');
                INSERT INTO contacts(id, owner_user_id, first_name, company_name, email, job_title) VALUES
                    (10, 2, 'Ada', 'Acme Retail', 'ada@example.test', 'Founder'),
                    (11, 2, 'Ben', 'Beta Group', 'ben@example.test', 'Director'),
                    (12, 2, 'Cara', 'Acme Retail', 'cara@example.test', 'Buyer'),
                    (20, 3, 'Other', 'Other Group', 'other@example.test', 'CEO'),
                    (30, NULL, 'Public', 'Public Group', 'public@example.test', 'CEO'),
                    (31, 2, 'Public owned', 'Public Group', 'public-owned@example.test', 'CEO'),
                    (40, 1, 'Admin owned', 'Admin Group', 'admin@example.test', 'CEO');
                UPDATE contacts SET pool_type='public' WHERE id IN (30, 31);
            """)

            class Db:
                @contextmanager
                def connect(self):
                    with conn.transaction():
                        yield conn

            repo = Repository(Db())
            yield conn, repo, OutreachBatchService(repo)
        finally:
            conn.rollback()


@pytest.mark.skipif(not QA_PG_DSN, reason="isolated QA PostgreSQL not configured")
class TestPostgresOutreachBatches:
    def test_detail_shows_approval_without_overriding_delivery_state(self, pg_batches):
        conn, repo, service = pg_batches
        batch = service.create(user=SALES, name="Approval", contact_ids=[10])["batch"]["id"]
        conn.execute("INSERT INTO email_drafts(id,contact_id,user_id,status) VALUES (100, 10, 2, 'approved')")
        repo.record_outreach_message(contact_id=10, user_id=2, campaign_id=batch,
                                    draft_id=100, channel="email", subject="Test", body="Body")
        assert service.detail(batch, user=SALES)["contacts"][0]["last_message_status"] == "approved"
        conn.execute("UPDATE outreach_messages SET status='failed' WHERE draft_id=100")
        assert service.detail(batch, user=SALES)["contacts"][0]["last_message_status"] == "failed"
        conn.execute("UPDATE outreach_messages SET status='replied', sent_at=NOW(), replied_at=NOW() WHERE draft_id=100")
        assert service.detail(batch, user=SALES)["contacts"][0]["last_message_status"] == "replied"

    def test_reopened_draft_exposes_original_campaign_after_later_import(self, pg_batches):
        _, repo, service = pg_batches
        batch = service.create(user=SALES, name="Original", contact_ids=[10])["batch"]["id"]
        draft = repo.save_email_draft(10, user_id=2, sequence_step=1, mode="custom", subject="Test", body="Body")
        repo.record_outreach_message(contact_id=10, user_id=2, campaign_id=batch, draft_id=draft["id"],
                                    channel="email", subject="Test", body="Body")
        service.create(user=SALES, name="Later", contact_ids=[10])
        assert repo.get_latest_email_draft(10, user_id=2)["campaign_id"] == batch
        assert repo.get_latest_email_draft(10, user_id=3) is None

    def test_owner_scope_and_explicit_message_attribution_without_rls(self, pg_batches):
        conn, _, service = pg_batches
        owned = service.create(user=SALES, name="Owned", contact_ids=[10, 11, 12])["batch"]["id"]
        other = service.create(user=OTHER_SALES, name="Other", contact_ids=[20])["batch"]["id"]
        # TEMP tables have no RLS: SQL predicates must work even for a database superuser.
        conn.execute("""INSERT INTO leads(source_type, campaign_id, contact_id, owner_user_id, source_row)
            VALUES ('transferred', %s, 20, 2, 4), ('duplicate', %s, 10, 2, 1)""", (owned, owned))
        conn.execute("""INSERT INTO outreach_messages
            (contact_id, campaign_id, user_id, subject, status, sent_at, replied_at, opened_at, metadata)
            VALUES
            (10, %s, 2, 'First', 'sent', NOW(), NOW(), NOW(), '{}'),
            (10, %s, 2, 'Follow up', 'sent', NOW(), NULL, NULL, '{"dry_run":false}'),
            (11, %s, 2, 'Historical campaign', 'sent', NOW(), NOW(), NOW(), '{}'),
            (11, NULL, 2, 'Unattributed', 'sent', NOW(), NOW(), NOW(), '{}'),
            (11, %s, 3, 'Other sender', 'sent', NOW(), NOW(), NOW(), '{}'),
            (20, %s, 2, 'Transferred contact', 'sent', NOW(), NOW(), NOW(), '{}'),
            (20, %s, 3, 'Other owner', 'sent', NOW(), NOW(), NOW(), '{}'),
            (12, %s, 2, 'Unsent', 'draft', NULL, NOW(), NOW(), '{}'),
            (12, %s, 2, 'Retry', 'failed', NULL, NULL, NULL, '{}'),
            (10, %s, 2, 'Dry latest', 'sent', NOW(), NOW(), NOW(), '{"dry_run":true}'),
            (11, %s, 2, 'Dry reply', 'sent', NOW(), NOW(), NOW(), '{"dry_run":"true"}'),
            (11, %s, 2, 'Dry failure', 'failed', NULL, NULL, NULL, '{"dry_run":true}')
            """, (owned, owned, other, owned, owned, other, owned, owned, owned, owned, owned))
        conn.execute("""INSERT INTO outreach_messages
            (contact_id, campaign_id, user_id, channel, subject, status, sent_at, replied_at, opened_at)
            VALUES (10, %s, 2, 'whatsapp', 'Non-email latest', 'sent', NOW(), NOW(), NOW()),
                   (11, %s, 2, 'phone', 'Non-email reply', 'sent', NOW(), NOW(), NOW()),
                   (11, %s, 2, 'whatsapp', 'Non-email failure', 'failed', NULL, NULL, NULL)
            """, (owned, owned, owned))
        conn.execute("""INSERT INTO email_events(contact_id, sequence_step, event_type, occurred_at)
            VALUES (11, 1, 'sent', NOW()-INTERVAL '30 days'),
                   (11, 1, 'replied', NOW()-INTERVAL '29 days')""")
        conn.execute("""INSERT INTO interactions(contact_id, interaction_type, occurred_at)
            VALUES (11, 'email_reply', NOW()-INTERVAL '29 days')""")
        conn.execute("INSERT INTO contact_research VALUES (10, 'Research', '[]', NOW())")
        conn.execute("UPDATE contacts SET email_status='invalid' WHERE id=12")
        conn.execute("""INSERT INTO followup_tasks(contact_id, assigned_user_id, title, due_at)
            VALUES (10, 3, 'Private other task', NOW()-INTERVAL '1 day'),
                   (10, 2, 'Owner task', NOW())""")

        listed = service.list(user=SALES)
        assert [row["id"] for row in listed] == [owned]
        assert {key: listed[0][key] for key in (
            "total_count", "sent_messages", "sent_contacts", "replied_contacts",
        )} == {"total_count": 3, "sent_messages": 2, "sent_contacts": 1, "replied_contacts": 1}
        assert [row["id"] for row in service.list(user=OTHER_SALES)] == [other]
        assert service.list(user={"id": 99, "role": "sales"}) == []
        for batch_id, user in ((other, SALES), (owned, OTHER_SALES), (owned, {"id": 99, "role": "sales"})):
            with pytest.raises(PermissionError, match="not accessible"):
                service.detail(batch_id, user=user)
        assert [row["id"] for row in service.detail(other, user=OTHER_SALES)["contacts"]] == [20]

        detail = service.detail(owned, user=SALES)
        assert detail["total"] == 3
        assert detail["summary"] == {
            "total_count": 3, "profiled_contacts": 1, "drafted_contacts": 2,
            "sent_contacts": 1, "sent_messages": 2, "replied_contacts": 1,
            "failed_messages": 1, "blocked_contacts": 1,
        }
        assert [row["id"] for row in detail["contacts"]] == [10, 11, 12]
        first, historical, unsent = detail["contacts"]
        assert first["latest_subject"] == "Follow up"
        assert first["next_task_title"] == "Owner task"
        assert (first["sent_count"], first["replied_count"], first["opened_count"]) == (2, 1, 1)
        assert historical["latest_subject"] is None
        assert historical["last_sent_at"] is None
        assert (historical["sent_count"], historical["replied_count"], historical["failed_count"]) == (0, 0, 0)
        assert (unsent["sent_count"], unsent["replied_count"], unsent["opened_count"], unsent["failed_count"]) == (0, 0, 0, 1)

    def test_pagination_and_search_totals(self, pg_batches):
        _, _, service = pg_batches
        batch = service.create(user=SALES, name="Search", contact_ids=[11, 10, 12])["batch"]["id"]
        page = service.detail(batch, user=SALES, limit=1, offset=1)
        assert [row["id"] for row in page["contacts"]] == [10]
        assert page["total"] == page["summary"]["total_count"] == 3
        filtered = service.detail(batch, user=SALES, search="  aCmE rEtAiL  ", limit=1, offset=1)
        assert [row["id"] for row in filtered["contacts"]] == [12]
        assert filtered["total"] == 2
        assert filtered["summary"] == page["summary"]
        beyond = service.detail(batch, user=SALES, search="acme", offset=2)
        assert beyond["contacts"] == [] and beyond["total"] == 2
        missing = service.detail(batch, user=SALES, search="not present")
        assert missing["contacts"] == [] and missing["total"] == 0
        assert missing["summary"] == page["summary"]
        email_match = service.detail(batch, user=SALES, search="BEN@EXAMPLE.TEST")
        assert [row["id"] for row in email_match["contacts"]] == [11]
        assert email_match["total"] == 1

    def test_creation_is_idempotent_and_preserves_original_roster_snapshot(self, pg_batches):
        conn, _, service = pg_batches
        original = {"10": {"Company": "Original CSV name", "row": 9, "notes": ["imported"]}}
        first = service.create(
            user=SALES, name="  Import  ", contact_ids=[11, 10, 11], source_ref="  roster.csv  ",
            source_rows=original, metadata={"auto_send": True, "workflow_version": 99, "tag": "original"},
        )
        before = conn.execute("SELECT * FROM leads ORDER BY source_row").fetchall()
        conn.execute("UPDATE contacts SET company_name='Changed', email='changed@example.test' WHERE id=10")
        second = service.create(
            user=SALES, name="Import", contact_ids=[10, 11], source_ref="roster.csv",
            source_rows={"10": {"Company": "Replacement"}}, metadata={"tag": "replacement"},
        )

        assert first["total"] == second["total"] == 2
        assert first["batch"]["id"] == second["batch"]["id"]
        assert first["batch"]["name"] == "Import"
        assert first["batch"]["channel"] == "outreach_batch"
        assert first["batch"]["owner_user_id"] == 2
        assert second["batch"]["metadata"] == {
            "source_ref": "roster.csv", "workflow_version": 1, "auto_send": False, "tag": "original",
        }
        assert conn.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()["n"] == 1
        assert conn.execute("SELECT * FROM leads ORDER BY source_row").fetchall() == before
        assert [row["contact_id"] for row in before] == [11, 10]
        assert [row["source_row"] for row in before] == [1, 2]
        assert before[0]["raw_data"]["original_row"] == {}
        assert before[1]["raw_data"]["original_row"] == original["10"]
        assert before[1]["raw_data"]["contact_snapshot"]["company_name"] == "Acme Retail"
        assert before[1]["normalized_email"] == "ada@example.test"
        detail = service.detail(first["batch"]["id"], user=SALES)
        assert detail["contacts"][1]["company_name"] == "Changed"
        assert detail["contacts"][1]["raw_data"] == before[1]["raw_data"]
        assert conn.execute("SELECT COUNT(*) AS n FROM outreach_messages").fetchone()["n"] == 0
        changed_roster = service.create(user=SALES, name="Import", contact_ids=[10, 12], source_ref="roster.csv")
        assert changed_roster["batch"]["id"] != first["batch"]["id"]

    @pytest.mark.parametrize("user, owned_contact, forbidden_contact", [
        (SALES, 10, 20), (SALES, 10, 30), (SALES, 10, 31), (SALES, 10, 999),
        ({"id": 1, "role": "admin"}, 40, 10), ({"id": 1, "role": "admin"}, 40, 30),
    ])
    def test_creation_rejects_entire_mixed_roster_without_writes(self, pg_batches, user, owned_contact, forbidden_contact):
        conn, _, service = pg_batches
        before = conn.execute("SELECT * FROM contacts ORDER BY id").fetchall()
        with pytest.raises(PermissionError, match="All batch contacts must belong"):
            service.create(user=user, name="Rejected", contact_ids=[owned_contact, forbidden_contact])
        assert conn.execute("SELECT COUNT(*) AS n FROM campaigns").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM outreach_messages").fetchone()["n"] == 0
        assert conn.execute("SELECT * FROM contacts ORDER BY id").fetchall() == before

    @pytest.mark.parametrize("send_evidence", [None, []])
    def test_send_upsert_keeps_draft_attribution_and_evidence_after_new_lead(self, pg_batches, send_evidence):
        conn, repo, service = pg_batches
        first = service.create(user=SALES, name="Original", contact_ids=[10])["batch"]["id"]
        evidence = [{"title": "Official research", "url": "https://example.test/about"}]
        draft = repo.record_outreach_message(
            contact_id=10, user_id=2, campaign_id=first, draft_id=100,
            channel="email", subject="Draft", body="Draft body", language="en", ai_model="qa-model",
            personalization_evidence=evidence, metadata={"mode": "personalized"},
        )
        original_lead = conn.execute("SELECT id FROM leads WHERE campaign_id=%s", (first,)).fetchone()["id"]
        assert (draft["campaign_id"], draft["lead_id"]) == (first, original_lead)
        later = service.create(user=SALES, name="Later", contact_ids=[10])["batch"]["id"]
        newest = conn.execute("SELECT * FROM leads WHERE contact_id=10 ORDER BY updated_at DESC, id DESC LIMIT 1").fetchone()
        assert newest["campaign_id"] == later and newest["id"] != original_lead

        sent = repo.record_outreach_message(
            contact_id=10, user_id=2, draft_id=100, channel="email", subject="Sent", body="Sent body",
            status="sent", provider="qa", provider_message_id="qa-message",
            personalization_evidence=send_evidence, metadata={"dry_run": False, "sender_email": "sender@example.test"},
        )

        assert sent["id"] == draft["id"]
        assert (sent["campaign_id"], sent["lead_id"]) == (first, original_lead)
        assert sent["personalization_evidence"] == evidence
        assert (sent["language"], sent["ai_model"]) == ("en", "qa-model")
        assert (sent["status"], sent["subject"], sent["body"], sent["provider_message_id"]) == (
            "sent", "Sent", "Sent body", "qa-message",
        )
        assert sent["metadata"] == {"mode": "personalized", "dry_run": False, "sender_email": "sender@example.test"}
        assert conn.execute("SELECT COUNT(*) AS n FROM outreach_messages").fetchone()["n"] == 1
