from types import SimpleNamespace

import sales_automation.health as health


class _Database:
    @staticmethod
    def is_available():
        return True


class _Repository:
    db = _Database()


def test_runtime_readiness_requires_an_available_sender(monkeypatch):
    monkeypatch.setattr(health, "readiness", lambda config: {"ready": True, "checks": []})

    def unavailable(self):
        raise RuntimeError("No sender account available today")

    monkeypatch.setattr(health.SenderPoolManager, "pick_sender", unavailable)
    result = health.check_readiness(SimpleNamespace(), _Repository())
    sender_check = next(item for item in result["checks"] if item["name"] == "sender_pool")

    assert result["ready"] is False
    assert sender_check == {
        "name": "sender_pool",
        "ok": False,
        "required": True,
        "message": "No sender account available today",
    }


def test_runtime_readiness_reports_sender_capacity(monkeypatch):
    monkeypatch.setattr(health, "readiness", lambda config: {"ready": True, "checks": []})
    monkeypatch.setattr(
        health.SenderPoolManager,
        "pick_sender",
        lambda self: {"email": "global@vertu.com", "daily_limit": 200, "send_count": 12},
    )

    result = health.check_readiness(SimpleNamespace(), _Repository())
    sender_check = next(item for item in result["checks"] if item["name"] == "sender_pool")

    assert result["ready"] is True
    assert sender_check["ok"] is True
    assert sender_check["message"] == "global@vertu.com available (188 remaining today)"
