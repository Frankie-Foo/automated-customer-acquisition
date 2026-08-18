from contextlib import contextmanager
from pathlib import Path

import pytest

from sales_automation.config import AppConfig
from sales_automation.contactout_queue import (
    ContactOutBlocked,
    ContactOutBridgeAdapter,
    ContactOutConflict,
    ContactOutQueueService,
    ContactOutRateLimited,
    ContactOutRun,
    contactout_bridge_configured,
)
from sales_automation.db import Repository, _merge_contact_candidates


def test_contactout_candidate_merge_accepts_legacy_string_values():
    merged = _merge_contact_candidates(
        ["legacy@example.com", None],
        [{"email": "new@example.com", "status": "valid", "confidence": 90}],
        "email",
    )

    assert merged == [
        {"email": "legacy@example.com", "status": "unverified"},
        {"email": "new@example.com", "status": "valid", "confidence": 90},
    ]


class FakeRepo:
    def __init__(self):
        self.contact = {
            "id": 7,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "linkedin_url": "https://www.linkedin.com/in/ada/?trk=test",
        }
        self.account = {"id": 3, "status": "active", "credential_ref": "contactout/ada"}
        self.jobs = []
        self.completed = []
        self.retry = []
        self.blocked = []
        self.failed = []
        self.quota_allowed = True
        self.candidates = []
        self.selected_account = self.account

    def get_contact(self, contact_id):
        return self.contact if contact_id == 7 else None

    def get_contactout_account(self, account_id, *, owner_user_id=None):
        return self.account if account_id == 3 else None

    def select_contactout_account(self, *, owner_user_id):
        return self.selected_account

    def list_contactout_candidates(self, *, limit, user=None):
        return self.candidates[:limit]

    def enqueue_contactout_job(self, **fields):
        self.jobs.append(fields)
        return {"id": 11, "status": "queued", **fields}

    def block_expired_contactout_jobs(self):
        return 0

    def claim_contactout_job(self):
        if not self.jobs:
            return None
        return {
            "id": 11,
            "status": "running",
            "account_id": 3,
            "contact_id": 7,
            "owner_user_id": 2,
            "lease_token": "lease",
            "idempotency_key": self.jobs[0]["idempotency_key"],
        }

    def reserve_contactout_job_quota(self, job_id, lease_token, *, global_limit):
        return "reserved" if self.quota_allowed else "daily_quota_exhausted"

    def complete_contactout_job(self, job_id, lease_token, *, status, result):
        self.completed.append((job_id, status, result))
        return True

    def retry_contactout_job(self, job_id, lease_token, error_code, *, retry_after_seconds):
        self.retry.append((job_id, error_code, retry_after_seconds))

    def block_contactout_job(self, job_id, lease_token, error_code):
        self.blocked.append((job_id, error_code))

    def fail_contactout_job(self, job_id, lease_token, error_code, *, consumed):
        self.failed.append((job_id, error_code, consumed))


class Adapter:
    def __init__(self, result=None, error=None):
        self.result = result or {}
        self.error = error
        self.calls = 0

    def enrich(self, account, contact, *, idempotency_key):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeHttp:
    def __init__(self, result=None, error=None):
        self.result = result or {"status": "matched"}
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return self.result


def config(limit=50):
    return AppConfig(raw={"contactout": {"global_daily_limit": limit}}, root_dir=Path("."))


def test_sales_enqueue_policy_fixes_server_controlled_fields():
    sql = (Path(__file__).parents[1] / "migrations" / "042_contactout_queue_fencing.sql").read_text(encoding="utf-8")

    for clause in (
        "operation = 'person_enrich'",
        "status = 'queued'",
        "priority = 0",
        "attempts = 0",
        "max_attempts = 3",
        "quota_units = 1",
        "quota_reserved = FALSE",
    ):
        assert clause in sql


def test_enqueue_is_stable_and_requires_linkedin_url():
    repo = FakeRepo()
    service = ContactOutQueueService(config(), repo, adapter=Adapter())

    first = service.enqueue(7, owner_user_id=2, account_id=3)
    second = service.enqueue(7, owner_user_id=2, account_id=3)
    assert first["idempotency_key"] == second["idempotency_key"]
    assert len(first["idempotency_key"]) == 64

    repo.contact["linkedin_url"] = ""
    with pytest.raises(ValueError, match="linkedin_url_required"):
        service.enqueue(7, owner_user_id=2, account_id=3)


def test_enqueue_conflict_has_stable_error_type():
    repo = FakeRepo()

    def conflict(**fields):
        raise ValueError("contactout_job_conflict")

    repo.enqueue_contactout_job = conflict
    service = ContactOutQueueService(config(), repo, adapter=Adapter())

    with pytest.raises(ContactOutConflict, match="contactout_job_conflict"):
        service.enqueue(7, owner_user_id=2, account_id=3)


def test_auto_enqueue_uses_assigned_account_without_manual_account_id():
    repo = FakeRepo()
    repo.candidates = [{"id": 7, "owner_user_id": 2}]
    repo.selected_account = {"id": 3, "status": "active"}
    service = ContactOutQueueService(config(), repo, adapter=Adapter())

    result = service.auto_enqueue(40, user={"id": 2, "role": "sales"})

    assert result["queued"] == 1
    assert result["skipped"] == []
    assert repo.jobs[0]["account_id"] == 3
    assert repo.jobs[0]["owner_user_id"] == 2


def test_auto_enqueue_reports_no_authorized_account_without_calling_provider():
    repo = FakeRepo()
    repo.candidates = [{"id": 7, "owner_user_id": 2}]
    repo.selected_account = None
    adapter = Adapter()
    service = ContactOutQueueService(config(), repo, adapter=adapter)

    result = service.auto_enqueue(1, user={"id": 2, "role": "sales"})

    assert result["queued"] == 0
    assert result["skipped"] == [{"contact_id": 7, "reason": "contactout_account_unavailable"}]
    assert adapter.calls == 0


def test_exact_match_is_structured_for_conservative_promotion():
    repo = FakeRepo()
    service = ContactOutQueueService(
        config(),
        repo,
        adapter=Adapter({
            "status": "matched",
            "match_confidence": 91,
            "emails": [{"email": "ADA@EXAMPLE.COM", "status": "valid", "category": "personal_work"}],
            "phones": [{"phone": "+44 20 1234 5678", "confidence": 85}],
        }),
    )
    service.enqueue(7, owner_user_id=2, account_id=3)
    run = service.run_next()

    assert run.status == "succeeded"
    result = repo.completed[0][2]
    assert result["review_required"] is False
    assert result["email_candidates"][0]["email"] == "ada@example.com"


def test_explicit_zero_candidate_confidence_is_not_inherited():
    repo = FakeRepo()
    service = ContactOutQueueService(
        config(),
        repo,
        adapter=Adapter({
            "status": "matched",
            "match_confidence": 95,
            "emails": [{
                "email": "ada@example.com",
                "status": "valid",
                "category": "personal_work",
                "confidence": 0,
            }],
        }),
    )
    service.enqueue(7, owner_user_id=2, account_id=3)

    service.run_next()

    assert repo.completed[0][2]["email_candidates"][0]["confidence"] == 0


def test_low_confidence_requires_review():
    repo = FakeRepo()
    service = ContactOutQueueService(
        config(), repo,
        adapter=Adapter({"status": "matched", "match_confidence": 65, "emails": ["ada@example.com"]}),
    )
    service.enqueue(7, owner_user_id=2, account_id=3)
    run = service.run_next()

    assert run.review_required is True
    assert repo.completed[0][2]["match_status"] == "review"


def test_quota_denial_does_not_call_provider():
    repo = FakeRepo()
    repo.quota_allowed = False
    adapter = Adapter()
    service = ContactOutQueueService(config(1), repo, adapter=adapter)
    service.enqueue(7, owner_user_id=2, account_id=3)

    run = service.run_next()

    assert run.status == "retry_wait"
    assert adapter.calls == 0
    assert repo.retry[0][1] == "daily_quota_exhausted"


def test_zero_global_limit_fails_closed():
    repo = FakeRepo()

    def reserve(job_id, lease_token, *, global_limit):
        return "reserved" if global_limit > 0 else "daily_quota_exhausted"

    repo.reserve_contactout_job_quota = reserve
    adapter = Adapter()
    service = ContactOutQueueService(config(0), repo, adapter=adapter)
    service.enqueue(7, owner_user_id=2, account_id=3)

    run = service.run_next()

    assert run.status == "retry_wait"
    assert adapter.calls == 0


def test_run_many_stops_when_queue_is_empty():
    service = ContactOutQueueService(config(), FakeRepo(), adapter=Adapter())
    queued = iter([ContactOutRun(1, "succeeded"), ContactOutRun(2, "no_match"), None])
    service.run_next = lambda: next(queued)

    assert service.run_many(10) == [
        {"job_id": 1, "status": "succeeded", "review_required": False, "error_code": None},
        {"job_id": 2, "status": "no_match", "review_required": False, "error_code": None},
    ]


def test_bridge_uses_one_idempotent_request():
    http = FakeHttp()
    cfg = AppConfig(
        raw={
            "apis": {"contactout_bridge_key": "bridge-secret"},
            "contactout": {"bridge_url": "https://bridge.internal/"},
        },
        root_dir=Path("."),
    )

    ContactOutBridgeAdapter(cfg, http=http).enrich(
        {"credential_ref": "contactout/ada"},
        {"id": 7, "linkedin_url": "https://linkedin.com/in/ada"},
        idempotency_key="job-key",
    )

    assert len(http.calls) == 1
    method, url, kwargs = http.calls[0]
    assert (method, url, kwargs["retries"]) == ("POST", "https://bridge.internal/enrich", 1)
    assert kwargs["headers"]["Idempotency-Key"] == "job-key"


def test_bridge_configuration_requires_url_and_secret():
    assert not contactout_bridge_configured(config())
    assert contactout_bridge_configured(AppConfig(raw={
        "apis": {"contactout_bridge_key": "secret"},
        "contactout": {"bridge_url": "https://bridge.internal"},
    }, root_dir=Path(".")))


def test_bridge_maps_http_429_to_rate_limit():
    http = FakeHttp(error=RuntimeError("HTTP request failed after 1 attempts: HTTP 429"))
    cfg = AppConfig(
        raw={
            "apis": {"contactout_bridge_key": "bridge-secret"},
            "contactout": {"bridge_url": "https://bridge.internal"},
        },
        root_dir=Path("."),
    )

    with pytest.raises(ContactOutRateLimited):
        ContactOutBridgeAdapter(cfg, http=http).enrich(
            {"credential_ref": "contactout/ada"},
            {"id": 7},
            idempotency_key="job-key",
        )

    assert len(http.calls) == 1


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (ContactOutRateLimited(120), "retry_wait", "rate_limited"),
        (ContactOutBlocked("challenge_required"), "blocked", "challenge_required"),
    ],
)
def test_provider_controls_stop_or_retry_same_account(error, expected_status, expected_code):
    repo = FakeRepo()
    service = ContactOutQueueService(config(), repo, adapter=Adapter(error=error))
    service.enqueue(7, owner_user_id=2, account_id=3)

    run = service.run_next()

    assert run.status == expected_status
    assert run.error_code == expected_code
    assert (repo.retry or repo.blocked)[0][0] == 11


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _AccountDb:
    def __init__(self, responses):
        self.responses = iter(responses)

    @contextmanager
    def connect(self):
        db = self

        class Conn:
            def execute(self, *_args, **_kwargs):
                return _Rows(next(db.responses, []))

        yield Conn()


def test_disabling_same_assignee_fences_existing_jobs():
    repo = Repository(_AccountDb([
        [{"id": 3, "assigned_user_id": 2, "status": "active"}],
        [{"id": 3, "assigned_user_id": 2, "status": "disabled"}],
    ]))
    fenced = []
    repo._fence_contactout_account_jobs = fenced.append

    repo.upsert_contactout_account(
        account_key="account-3",
        display_name="Account 3",
        masked_identity="a***@example.com",
        credential_ref="contactout/account-3",
        assigned_user_id=2,
        daily_limit=5,
        authorized_by_user_id=1,
        status="disabled",
    )

    assert fenced == [3]


def test_provider_challenge_fences_other_jobs_after_account_update():
    repo = Repository(_AccountDb([
        [{"id": 11, "account_id": 3, "quota_reserved": False}],
        [],
        [],
    ]))
    repo._settle_contactout_quota = lambda *_args, **_kwargs: None
    fenced = []
    repo._fence_contactout_account_jobs = fenced.append

    repo.block_contactout_job(11, "lease", "challenge_required")

    assert fenced == [3]


def test_fence_rechecks_account_after_locking_jobs():
    repo = Repository(_AccountDb([
        [{"id": 11, "owner_user_id": 2, "quota_reserved": True}],
        [{"status": "active", "assigned_user_id": 2}],
    ]))
    settled = []
    repo._settle_contactout_quota = lambda *_args, **_kwargs: settled.append(True)

    repo._fence_contactout_account_jobs(3)

    assert settled == []


def test_contactout_candidate_order_uses_existing_contact_timestamps():
    class Cursor:
        def fetchall(self):
            return []

    class Connection:
        def __init__(self):
            self.query = ""

        def execute(self, query, _params):
            self.query = query
            return Cursor()

    class Database:
        def __init__(self):
            self.connection = Connection()

        @contextmanager
        def connect(self):
            yield self.connection

    database = Database()

    Repository(database).list_contactout_candidates(limit=10, user={"id": 2, "role": "sales"})

    assert "COALESCE(c.profile_updated_at, c.enriched_at, c.created_at)" in database.connection.query
    assert "c.updated_at" not in database.connection.query
    assert "LOWER(c.linkedin_url) LIKE '%%linkedin.com/in/%%'" in database.connection.query
    assert "available_account.assigned_user_id = c.owner_user_id" in database.connection.query
    assert "available_account.status = 'active'" in database.connection.query
    assert "account_usage.reserved_units + account_usage.used_units" in database.connection.query
    assert "pending_job.status IN ('queued', 'retry_wait')" in database.connection.query


def test_contactout_account_selection_uses_actual_usage_columns():
    class Cursor:
        def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.query = ""

        def execute(self, query, _params):
            self.query = query
            return Cursor()

    class Database:
        def __init__(self):
            self.connection = Connection()

        @contextmanager
        def connect(self):
            yield self.connection

    database = Database()

    Repository(database).select_contactout_account(owner_user_id=2)

    assert "account_usage.reserved_units" in database.connection.query
    assert "account_usage.used_units" in database.connection.query
    assert "account_usage.consumed_units" not in database.connection.query
