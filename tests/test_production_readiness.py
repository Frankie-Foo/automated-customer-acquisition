from types import SimpleNamespace

from sales_automation.auth import clear_session_cookie, session_cookie
from sales_automation.production import readiness


def cfg(raw):
    return SimpleNamespace(
        raw=raw,
        apis=raw.get("apis", {}),
        sender=raw.get("sender", {}),
        database=raw.get("database", {}),
    )


def base_raw():
    return {
        "database": {"host": "postgres", "dbname": "overseaspdca"},
        "apis": {
            "google_cse_key": "key",
            "google_cse_id": "cx",
            "hunter_key": "hunter",
            "resend_key": "resend",
            "deepseek_key": "deepseek",
        },
        "app": {"public_base_url": "https://sales.frelys.xyz"},
        "llm": {"provider": "deepseek"},
        "quotas": {"global_daily_send_limit": 3000, "global_daily_source_limit": 3000},
        "sender_pool": {
            "accounts": [
                {"name": "sales01", "email": "sales01@mail.frelys.xyz", "dry_run": False, "active": True},
                {"name": "sales02", "email": "sales02@mail.frelys.xyz", "dry_run": False, "active": True},
            ]
        },
    }


def test_readiness_accepts_google_cse_and_sender_pool(monkeypatch):
    monkeypatch.setenv("SALESBOT_ADMIN_PASSWORD", "long-random-password")

    data = readiness(cfg(base_raw()))
    checks = {item["name"]: item for item in data["checks"]}

    assert checks["lead_source"]["ok"] is True
    assert checks["sender_email"]["ok"] is True
    assert checks["dry_run"]["ok"] is True


def test_readiness_rejects_sender_pool_dry_run(monkeypatch):
    monkeypatch.setenv("SALESBOT_ADMIN_PASSWORD", "long-random-password")
    raw = base_raw()
    raw["sender_pool"]["accounts"][0]["dry_run"] = True

    checks = {item["name"]: item for item in readiness(cfg(raw))["checks"]}

    assert checks["dry_run"]["ok"] is False


def test_secure_cookie_flag_is_optional():
    assert "Secure" not in session_cookie("token")
    assert "Secure" in session_cookie("token", secure=True)
    assert "Secure" in clear_session_cookie(secure=True)
