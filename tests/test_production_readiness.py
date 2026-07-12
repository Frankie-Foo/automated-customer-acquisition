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
        "app": {"public_base_url": "https://sales.frelys.xyz", "tracking_signing_secret": "tracking-secret-at-least-24-chars"},
        "webhooks": {"inbound_email_secret": "inbound-secret-at-least-24-chars"},
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


def test_centralized_identity_requires_domains_and_routing_secret(monkeypatch):
    monkeypatch.setenv("SALESBOT_ADMIN_PASSWORD", "long-random-password")
    raw = base_raw()
    raw["outbound_identity"] = {
        "mode": "centralized_alias",
        "sending_domain": "outreach.vertu.test",
        "reply_domain": "reply.outreach.vertu.test",
        "routing_secret": "routing-secret-at-least-24-characters",
    }

    checks = {item["name"]: item for item in readiness(cfg(raw))["checks"]}

    assert checks["outbound_identity"]["ok"] is True
    assert checks["outbound_identity"]["required"] is True


def test_readiness_accepts_smtp_transport_without_resend_for_sending(monkeypatch):
    monkeypatch.setenv("SALESBOT_ADMIN_PASSWORD", "long-random-password")
    raw = base_raw()
    raw["apis"].pop("resend_key")
    raw["sender_pool"] = {"accounts": []}
    raw["sender"] = {"provider": "smtp", "email": "partnerships@outreach.vertu.test", "dry_run": False}
    raw["smtp"] = {"host": "smtp.example.test", "username": "smtp-user", "password": "client-password"}

    checks = {item["name"]: item for item in readiness(cfg(raw))["checks"]}

    assert checks["mail_transport"]["ok"] is True
