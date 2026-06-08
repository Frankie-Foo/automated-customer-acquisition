from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sales_automation.sender_pool import SenderPoolManager


class FakeRepo:
    def __init__(self):
        self.accounts = {}
        self.usage = {}

    def ensure_sender_account(self, account):
        sender_id = len(self.accounts) + 1
        existing = self.accounts.get(account["email"])
        if existing:
            return existing
        row = {**account, "id": sender_id, "active": True, "created_at": account.get("created_at") or datetime.now(UTC)}
        self.accounts[account["email"]] = row
        self.usage[sender_id] = 0
        return row

    def sender_usage_today(self, sender_id):
        return {"sender_id": sender_id, "send_count": self.usage.get(sender_id, 0)}

    def sender_total_sent_today(self):
        return sum(self.usage.values())

    def record_sender_send(self, sender_id):
        self.usage[sender_id] += 1


def cfg(raw=None, sender=None):
    return SimpleNamespace(raw=raw or {}, sender=sender or {"name": "A", "email": "a@example.com", "provider": "resend", "daily_limit": 100, "dry_run": True})


def test_fallback_single_sender():
    sender = SenderPoolManager(cfg(), FakeRepo()).pick_sender()

    assert sender["email"] == "a@example.com"


def test_round_robin_rotation():
    repo = FakeRepo()
    config = cfg(raw={"sender_pool": {"accounts": [
        {"name": "A", "email": "a@example.com", "provider": "resend", "daily_limit": 100},
        {"name": "B", "email": "b@example.com", "provider": "resend", "daily_limit": 100},
    ]}})
    pool = SenderPoolManager(config, repo)

    first = pool.pick_sender()
    pool.record_send(first)
    second = pool.pick_sender()

    assert first["email"] != second["email"]


def test_sender_daily_limit_excludes_account():
    repo = FakeRepo()
    config = cfg(raw={"sender_pool": {"accounts": [
        {"name": "A", "email": "a@example.com", "provider": "resend", "daily_limit": 1},
        {"name": "B", "email": "b@example.com", "provider": "resend", "daily_limit": 100},
    ]}})
    pool = SenderPoolManager(config, repo)
    first = pool.pick_sender()
    pool.record_send(first)
    second = pool.pick_sender()

    assert second["email"] == "b@example.com"


def test_warmup_limit_grows_by_account_age():
    repo = FakeRepo()
    created = datetime.now(UTC) - timedelta(days=2)
    config = cfg(raw={"sender_pool": {"accounts": [
        {"name": "A", "email": "a@example.com", "provider": "resend", "daily_limit": 100, "warmup_stage": "warmup", "created_at": created},
    ]}})
    sender = SenderPoolManager(config, repo).pick_sender()

    assert sender["daily_limit"] == 30
