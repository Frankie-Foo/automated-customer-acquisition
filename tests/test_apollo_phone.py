from pathlib import Path

import pytest

from sales_automation.apollo_phone import (
    ApolloApiAdapter,
    ApolloPhoneQueueService,
    _normalize_webhook,
    apollo_phone_configured,
)
from sales_automation.config import AppConfig


def _config(**apollo):
    raw = {
        "app": {"public_base_url": "https://sales.example.com"},
        "apis": {"apollo_key": "test-key"},
        "webhooks": {"apollo_secret": "a" * 32},
        "apollo_phone": {
            "enabled": True,
            "global_daily_credit_limit": 90,
            "max_credits_per_lookup": 9,
            **apollo,
        },
    }
    return AppConfig(raw=raw, root_dir=Path("."))


class _Http:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return {"request_id": "request-1"}


def test_apollo_adapter_requests_phone_only_without_waterfalls():
    http = _Http()
    adapter = ApolloApiAdapter(_config(), http=http)

    adapter.request_phone(
        {"linkedin_url": "https://www.linkedin.com/in/person"},
        webhook_url="https://sales.example.com/webhooks/apollo?job_id=1&token=x",
    )

    method, url, kwargs = http.calls[0]
    assert method == "POST"
    assert "reveal_phone_number=true" in url
    assert "reveal_personal_emails=false" in url
    assert "run_waterfall_email=false" in url
    assert "run_waterfall_phone=false" in url
    assert kwargs["headers"]["x-api-key"] == "test-key"


def test_apollo_configuration_fails_closed_without_budget_or_secret():
    assert apollo_phone_configured(_config())
    no_secret = _config()
    no_secret.raw["webhooks"]["apollo_secret"] = "short"
    assert not apollo_phone_configured(no_secret)
    disabled = _config(enabled=False)
    assert not apollo_phone_configured(disabled)


class _Repo:
    def __init__(self):
        self.job = {
            "id": 7,
            "idempotency_key": "1" * 64,
            "contact_id": 2,
            "owner_user_id": 3,
            "lease_token": "lease",
            "quota_units": 9,
        }
        self.reservation = "reserved"
        self.calls = []

    def expire_apollo_phone_jobs(self):
        return 0

    def claim_apollo_phone_job(self):
        self.calls.append("claim")
        return self.job

    def reserve_apollo_phone_quota(self, job_id, lease_token, *, global_limit):
        self.calls.append(("reserve", job_id, lease_token, global_limit))
        return self.reservation

    def get_contact(self, _contact_id):
        return {"linkedin_url": "https://www.linkedin.com/in/person"}

    def mark_apollo_phone_awaiting_webhook(self, *args, **kwargs):
        self.calls.append(("await", args, kwargs))
        return True

    def fail_apollo_phone_dispatch(self, *args, **kwargs):
        self.calls.append(("fail", args, kwargs))

    def complete_apollo_phone_no_match(self, *args, **kwargs):
        self.calls.append(("no_match", args, kwargs))
        return True

    def get_apollo_phone_job(self, _job_id):
        return self.job

    def complete_apollo_phone_webhook(self, *args, **kwargs):
        self.calls.append(("webhook", args, kwargs))
        return {"job": self.job, "duplicate": False}


class _Adapter:
    def __init__(self, response=None, error=None):
        self.response = response or {"request_id": "request-1"}
        self.error = error
        self.calls = 0

    def request_phone(self, _contact, *, webhook_url):
        self.calls += 1
        assert webhook_url.startswith("https://sales.example.com/webhooks/apollo?")
        if self.error:
            raise self.error
        return self.response


def test_quota_denial_prevents_apollo_call():
    repo = _Repo()
    repo.reservation = "daily_quota_exhausted"
    adapter = _Adapter()

    result = ApolloPhoneQueueService(_config(), repo, adapter=adapter).dispatch_next()

    assert result.status == "blocked"
    assert adapter.calls == 0


def test_dispatch_reserves_before_request_and_waits_for_webhook():
    repo = _Repo()
    adapter = _Adapter()

    result = ApolloPhoneQueueService(_config(), repo, adapter=adapter).dispatch_next()

    assert result.status == "awaiting_webhook"
    assert repo.calls[0] == "claim"
    assert repo.calls[1][0] == "reserve"
    assert repo.calls[2][0] == "await"


def test_unknown_dispatch_failure_is_charged_and_not_retried():
    repo = _Repo()
    adapter = _Adapter(error=TimeoutError("unknown provider outcome"))

    result = ApolloPhoneQueueService(_config(), repo, adapter=adapter).dispatch_next()

    assert result.status == "blocked"
    failure = next(call for call in repo.calls if isinstance(call, tuple) and call[0] == "fail")
    assert failure[2]["charge_reserved"] is True


def test_webhook_token_and_actual_credits_are_forwarded():
    repo = _Repo()
    service = ApolloPhoneQueueService(_config(), repo, adapter=_Adapter())
    payload = {
        "credits_consumed": 8,
        "request_id": "request-1",
        "people": [{"phone_numbers": [{"sanitized_number": "+12025550123", "status_cd": "valid_number", "type_cd": "mobile", "confidence_cd": "high"}]}],
    }

    result = service.process_webhook(7, payload, service.callback_token(repo.job))

    assert result["duplicate"] is False
    call = next(item for item in repo.calls if isinstance(item, tuple) and item[0] == "webhook")
    assert call[2]["credits_consumed"] == 8
    assert call[2]["phone_candidates"][0]["phone"] == "+12025550123"
    with pytest.raises(PermissionError):
        service.process_webhook(7, payload, "bad-token")


def test_webhook_unknown_credit_cost_charges_reserved_units():
    repo = _Repo()
    repo.job["quota_units"] = 7
    service = ApolloPhoneQueueService(_config(), repo, adapter=_Adapter())

    service.process_webhook(7, {"people": []}, service.callback_token(repo.job))

    call = next(item for item in repo.calls if isinstance(item, tuple) and item[0] == "webhook")
    assert call[2]["credits_consumed"] == 7


def test_no_match_invalid_credit_cost_charges_reserved_units():
    repo = _Repo()
    repo.job["quota_units"] = 6
    adapter = _Adapter(response={"person": None, "credits_consumed": "unknown"})

    result = ApolloPhoneQueueService(_config(), repo, adapter=adapter).dispatch_next()

    assert result.status == "no_match"
    call = next(item for item in repo.calls if isinstance(item, tuple) and item[0] == "no_match")
    assert call[2]["credits_consumed"] == 6


def test_webhook_phone_normalization_deduplicates_numbers():
    result = _normalize_webhook({
        "credits_consumed": 99,
        "people": [{"phone_numbers": [
            {"raw_number": "+1 202 555 0123", "status": "unknown"},
            {"sanitized_number": "+12025550123", "status_cd": "valid_number", "confidence_cd": "high"},
        ]}],
    })

    assert result["credits_consumed"] == 9
    assert len(result["phone_candidates"]) == 1
    assert result["phone_candidates"][0]["status"] == "valid"


def test_webhook_normalizes_nested_single_person_payload():
    result = _normalize_webhook({
        "data": {
            "credits_consumed": 8,
            "request_id": "nested-request",
            "person": {
                "phone_numbers": [{
                    "sanitized_number": "+971501234567",
                    "status_cd": "valid_number",
                    "type_cd": "mobile",
                }],
            },
        },
    })

    assert result["credits_consumed"] == 8
    assert result["provider_request_id"] == "nested-request"
    assert result["phone_candidates"][0]["phone"] == "+971501234567"


def test_webhook_ignores_malformed_provider_values():
    result = _normalize_webhook({
        "credits_consumed": "invalid",
        "people": [None, {"phone_numbers": [None, {"number": "short"}]}],
    })

    assert result["credits_consumed"] == 9
    assert result["phone_candidates"] == []


def test_webhook_preserves_explicit_zero_credit_cost():
    result = _normalize_webhook({"credits_consumed": 0})

    assert result["credits_consumed"] == 0


def test_webhook_invalid_numeric_credit_values_use_reservation():
    assert _normalize_webhook({"credits_consumed": -1}, default_credits=7)["credits_consumed"] == 7
    assert _normalize_webhook({"credits_consumed": 10}, default_credits=7)["credits_consumed"] == 7
    assert _normalize_webhook({"credits_consumed": False}, default_credits=7)["credits_consumed"] == 7


def test_apollo_dispatch_contact_fence_guards_transfer_and_delete():
    sql = Path("migrations/047_apollo_dispatch_contact_fence.sql").read_text(encoding="utf-8")

    assert "BEFORE UPDATE OF owner_user_id, pool_type OR DELETE ON contacts" in sql
    assert "job.status = 'dispatching'" in sql
    assert "ERRCODE = '55000'" in sql
