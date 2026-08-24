from pathlib import Path
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


def test_enabled_apollo_phone_is_a_required_readiness_check(monkeypatch):
    monkeypatch.setenv("SALESBOT_ADMIN_PASSWORD", "long-random-password")
    raw = base_raw()
    raw["apollo_phone"] = {"enabled": True, "global_daily_credit_limit": 20}

    data = readiness(cfg(raw))
    check = next(item for item in data["checks"] if item["name"] == "apollo_phone")

    assert check["required"] is True
    assert check["ok"] is False
    assert data["ready"] is False


def test_disabled_apollo_phone_remains_optional(monkeypatch):
    monkeypatch.setenv("SALESBOT_ADMIN_PASSWORD", "long-random-password")

    data = readiness(cfg(base_raw()))
    check = next(item for item in data["checks"] if item["name"] == "apollo_phone")

    assert check["required"] is False
    assert data["ready"] is True


def test_production_compose_runs_safe_scheduler_worker():
    for path in (
        "deployment/docker-compose.production.yml",
        "deployment/docker-compose.external-db.yml",
    ):
        compose = Path(path).read_text(encoding="utf-8")

        assert "scheduler-worker:" in compose
        assert 'SALESBOT_SCHEDULER_SEND_LIMIT:-0' in compose
        assert "salesbot scheduler" in compose
        assert "healthcheck:" in compose
        assert "touch /tmp/salesbot-worker-success" in compose
        assert "|| true" not in compose


def test_bundled_postgres_backup_is_atomic_and_fails_closed():
    compose = Path("deployment/docker-compose.production.yml").read_text(encoding="utf-8")

    assert "pg_dump -Fc" in compose
    assert "if pg_dump -Fc" in compose
    assert "salesbot_$$(date +%F_%H%M%S).dump.tmp" in compose
    assert "mv \"$${backup_tmp}\" \"$${backup_final}\"" in compose
    assert "touch /backups/.last_success" in compose
    assert "| gzip" not in compose


def test_runtime_config_maps_contactout_bridge():
    runtime_config = Path("config.example.yaml").read_text(encoding="utf-8")

    assert "contactout_bridge_key: ${CONTACTOUT_BRIDGE_KEY}" in runtime_config
    assert "bridge_url: ${CONTACTOUT_BRIDGE_URL}" in runtime_config
    assert "scheduler_limit: 40" in runtime_config


def test_frontend_routes_customer_workspace_to_outreach_consistently():
    app = Path("frontend/src/App.jsx").read_text(encoding="utf-8")
    legacy = Path("frontend/src/legacy-controller.js").read_text(encoding="utf-8")

    assert '"customer-workspace": "outreach"' in app
    assert '"customer-workspace": "outreach"' in legacy


def test_frontend_surfaces_background_failures_and_auto_replies():
    workbench = Path("frontend/src/Workbench.jsx").read_text(encoding="utf-8")
    workspace = Path("frontend/src/CustomerWorkspace.jsx").read_text(encoding="utf-8")
    sent_emails = Path("frontend/src/SentEmails.jsx").read_text(encoding="utf-8")

    assert ".catch(() => {})" not in workbench
    assert ".catch(() => {})" not in workspace
    assert 'auto_reply: "自动回复"' in sent_emails
