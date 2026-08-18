from __future__ import annotations

import json
import threading
import time
from email.message import Message

from contactout_bridge.contactout_client import ContactOutClient, ProviderError
from contactout_bridge.server import BridgeApp, IdempotencyStore, load_credentials


class FakePool:
    credentials = {"account-1": {"email": "a@example.com", "password": "secret"}}

    def __init__(self):
        self.calls = 0

    def enrich(self, credential_ref, contact):
        self.calls += 1
        assert credential_ref == "account-1"
        return {"status": "matched", "emails": [{"email": "lead@example.com", "category": "personal_work"}], "phones": []}


def headers(key="x" * 32, idempotency="job-1"):
    value = Message()
    value["Authorization"] = f"Bearer {key}"
    value["Idempotency-Key"] = idempotency
    return value


def test_bridge_auth_and_idempotency(tmp_path):
    pool = FakePool()
    app = BridgeApp(key="x" * 32, pool=pool, store=IdempotencyStore(str(tmp_path / "idempotency.sqlite3")))
    body = json.dumps({"credential_ref": "account-1", "contact": {"first_name": "Ada"}}).encode()

    assert app.enrich(headers("wrong"), body)[0] == 401
    first = app.enrich(headers(), body)
    second = app.enrich(headers(), body)

    assert first == second
    assert pool.calls == 1


def test_bridge_serializes_concurrent_duplicate_request(tmp_path):
    class SlowPool(FakePool):
        def enrich(self, credential_ref, contact):
            time.sleep(0.05)
            return super().enrich(credential_ref, contact)

    pool = SlowPool()
    app = BridgeApp(key="x" * 32, pool=pool, store=IdempotencyStore(str(tmp_path / "idempotency.sqlite3")))
    body = json.dumps({"credential_ref": "account-1", "contact": {"first_name": "Ada"}}).encode()
    results = []

    threads = [threading.Thread(target=lambda: results.append(app.enrich(headers(), body))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert results[0] == results[1]
    assert pool.calls == 1


def test_bridge_does_not_cache_reauth(tmp_path):
    class ReauthPool(FakePool):
        def enrich(self, credential_ref, contact):
            self.calls += 1
            raise ProviderError("reauth_required")

    pool = ReauthPool()
    app = BridgeApp(key="x" * 32, pool=pool, store=IdempotencyStore(str(tmp_path / "idempotency.sqlite3")))
    body = json.dumps({"credential_ref": "account-1", "contact": {"first_name": "Ada"}}).encode()

    assert app.enrich(headers(), body) == (200, {"status": "reauth_required"})
    assert app.enrich(headers(), body) == (200, {"status": "reauth_required"})
    assert pool.calls == 2


def test_profile_selection_requires_exact_identity():
    profiles = [
        {"fullName": "Ada Lovelace", "company": "Analytical Engines", "liVanity": "ada-lovelace"},
        {"fullName": "Ada Lovelace", "company": "Other", "liVanity": "wrong-ada"},
    ]
    assert ContactOutClient._select_profile(
        profiles, {"linkedin_url": "https://linkedin.com/in/ada-lovelace"}
    )["liVanity"] == "ada-lovelace"
    assert ContactOutClient._select_profile(
        profiles,
        {"first_name": "Ada", "last_name": "Lovelace", "company_name": "Analytical Engines Ltd"},
    )["liVanity"] == "ada-lovelace"
    assert ContactOutClient._select_profile(
        profiles, {"first_name": "Ada", "last_name": "Lovelace", "company_name": "Unknown"}
    ) is None


def test_client_emits_queue_contract(monkeypatch):
    client = ContactOutClient()
    client._authenticated = True
    client._session = object()
    monkeypatch.setattr(client, "_search", lambda name: [
        {"fullName": "Ada Lovelace", "company": "Analytical Engines", "title": "Founder", "liVanity": "ada-lovelace"}
    ])
    monkeypatch.setattr(client, "_reveal", lambda vanity: {
        "emails": [{"value": "ada@example.com", "type": "work"}],
        "phones": [{"value": "+44 20 1234 5678"}],
        "credit_remaining": 4,
    })

    result = client.enrich({
        "first_name": "Ada",
        "last_name": "Lovelace",
        "company_name": "Analytical Engines",
        "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
    })

    assert result["status"] == "matched"
    assert result["match_confidence"] == 98
    assert result["emails"] == [{
        "email": "ada@example.com",
        "status": "unverified",
        "category": "personal_work",
        "confidence": 98,
    }]
    assert result["phones"][0]["phone"] == "+44 20 1234 5678"


def test_credentials_validation(tmp_path):
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": {"ref": {"email": "a@example.com", "password": "pw"}}}))
    path.chmod(0o600)
    assert load_credentials(str(path))["ref"]["email"] == "a@example.com"
