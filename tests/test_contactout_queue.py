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
)
from sales_automation.db import Repository


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

    def get_contact(self, contact_id):
        return self.contact if contact_id == 7 else None

    def get_contactout_account(self, account_id, *, owner_user_id=None):
        return self.account if account_id == 3 else None

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
