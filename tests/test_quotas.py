from types import SimpleNamespace

import pytest

from sales_automation.quotas import QuotaService


class FakeRepo:
    def __init__(self):
        self.user_usage = {"source_count": 0, "send_count": 0}
        self.global_usage_data = {"source_count": 0, "send_count": 0}

    def usage_for_user(self, user_id):
        return self.user_usage

    def global_usage(self):
        return self.global_usage_data

    def consume_user_and_global_quota(self, user_id, field, amount, user_limit, global_limit):
        if self.user_usage[field] + amount > user_limit:
            raise RuntimeError("user_daily_quota_exceeded")
        if self.global_usage_data[field] + amount > global_limit:
            raise RuntimeError("global_daily_quota_exceeded")
        self.user_usage[field] += amount
        self.global_usage_data[field] += amount
        return {"user_usage": self.user_usage, "global_usage": self.global_usage_data}

    def consume_global_quota(self, field, amount, limit):
        if self.global_usage_data[field] + amount > limit:
            raise RuntimeError("global_daily_quota_exceeded")
        self.global_usage_data[field] += amount
        return self.global_usage_data


def config():
    return SimpleNamespace(raw={"quotas": {"global_daily_send_limit": 3, "global_daily_source_limit": 5}})


def test_quota_consumes_user_and_global():
    repo = FakeRepo()
    result = QuotaService(config(), repo).consume({"id": 1, "daily_send_limit": 2}, "send", 2)

    assert result["user_usage"]["send_count"] == 2
    assert result["global_usage"]["send_count"] == 2
    assert result["remaining_user"] == 0


def test_user_quota_exceeded():
    repo = FakeRepo()
    repo.user_usage["send_count"] = 2

    with pytest.raises(RuntimeError, match="user_daily_quota_exceeded"):
        QuotaService(config(), repo).consume({"id": 1, "daily_send_limit": 2}, "send", 1)


def test_global_quota_exceeded():
    repo = FakeRepo()
    repo.global_usage_data["send_count"] = 3

    with pytest.raises(RuntimeError, match="global_daily_quota_exceeded"):
        QuotaService(config(), repo).consume({"id": 1, "daily_send_limit": 10}, "send", 1)


def test_consume_global_only_for_cli_scheduler():
    repo = FakeRepo()
    result = QuotaService(config(), repo).consume_global("send", 2)

    assert result["global_usage"]["send_count"] == 2
    assert result["remaining_global"] == 1
