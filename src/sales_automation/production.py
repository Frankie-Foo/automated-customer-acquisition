from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from .config import AppConfig


def readiness(config: AppConfig) -> dict[str, Any]:
    apis = config.apis
    sender = config.sender
    sender_pool = config.raw.get("sender_pool", {})
    llm = config.raw.get("llm", {})
    app = config.raw.get("app", {})
    quotas = config.raw.get("quotas", {})
    outbound_identity = config.raw.get("outbound_identity", {})
    smtp = config.raw.get("smtp", {})
    centralized_identity = str(outbound_identity.get("mode") or "").strip().lower() == "centralized_alias"
    checks = [
        _check("database", bool(config.database.get("host") and config.database.get("dbname")), "Database config is present"),
        _check(
            "lead_source",
            bool(apis.get("prospeo_key") or apis.get("ninjapear_key") or apis.get("brave_search_key") or apis.get("tavily_key") or (apis.get("google_cse_key") and apis.get("google_cse_id"))),
            "Prospeo, NinjaPear, Brave, Tavily, or Google CSE is required for automated lead sourcing",
        ),
        _check("enrichment", bool(apis.get("hunter_key") or apis.get("prospeo_key") or apis.get("ninjapear_key")), "Hunter, Prospeo, or NinjaPear key is required for email enrichment"),
        _check("social_enrichment", bool(apis.get("peopledb_key") or apis.get("pdl_key")), "PeopleDB or People Data Labs key is optional for social profile enrichment"),
        _check(
            "mail_transport",
            _mail_transport_ready(apis, sender, sender_pool, smtp),
            "Configure credentials for every active Resend, SendGrid, or SMTP sender",
        ),
        _check("sender_email", _sender_ready(sender, sender_pool), "At least one verified-domain sender email is required"),
        _check("dry_run", _dry_run_ready(sender, sender_pool), "Set sender dry_run=false only after sender domain is verified"),
        _check("public_url", _public_base_url_ready(app.get("public_base_url")), "Use a public HTTPS PUBLIC_BASE_URL for unsubscribe links, tracking pixels, and webhooks"),
        _check("tracking_security", len(str(app.get("tracking_signing_secret") or "")) >= 24, "TRACKING_SIGNING_SECRET must contain at least 24 characters"),
        _check(
            "reply_ingestion",
            _reply_ingestion_ready(config.raw.get("webhooks", {}), apis, centralized_identity),
            "Use the verified Resend webhook for centralized receiving, or configure INBOUND_EMAIL_WEBHOOK_SECRET for a mailbox bridge",
        ),
        _check(
            "outbound_identity",
            _outbound_identity_ready(outbound_identity),
            "Centralized identity requires sending/reply subdomains and a separate REPLY_ROUTING_SECRET",
            required=centralized_identity,
        ),
        _check("quotas", bool(quotas.get("global_daily_send_limit") and quotas.get("global_daily_source_limit")), "Global source/send quotas should be configured"),
        _check("admin_password", _admin_password_ready(), "Change SALESBOT_ADMIN_PASSWORD before production"),
        _check("llm", _llm_ready(apis, llm), "DeepSeek/OpenAI key is required for AI openers; fallback works without it"),
        _check("slack", bool(config.raw.get("notifications", {}).get("slack_webhook_url")), "Slack webhook is optional for reply/error notifications"),
    ]
    required = [c for c in checks if c["required"]]
    ready = all(c["ok"] for c in required)
    return {"ready": ready, "checks": checks}


def _check(name: str, ok: bool, message: str, *, required: bool = True) -> dict[str, Any]:
    if name in {"llm", "slack", "social_enrichment"}:
        required = False
    return {"name": name, "ok": ok, "message": message, "required": required}


def _valid_sender(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    return not email.endswith("@outreach-domain.com") and email != "you@outreach-domain.com"


def _sender_ready(sender: dict[str, Any], sender_pool: dict[str, Any]) -> bool:
    accounts = [account for account in sender_pool.get("accounts", []) if account.get("active", True)]
    if accounts:
        return all(_valid_sender(account.get("email")) for account in accounts)
    return _valid_sender(sender.get("email"))


def _dry_run_ready(sender: dict[str, Any], sender_pool: dict[str, Any]) -> bool:
    accounts = [account for account in sender_pool.get("accounts", []) if account.get("active", True)]
    if accounts:
        return all(account.get("dry_run") is False for account in accounts)
    return sender.get("dry_run") is False


def _llm_ready(apis: dict[str, Any], llm: dict[str, Any]) -> bool:
    provider = llm.get("provider", "deepseek")
    return bool(apis.get(f"{provider}_key") or apis.get("openai_key"))


def _public_base_url_ready(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    if parsed.scheme != "https":
        return False
    return parsed.hostname not in {"127.0.0.1", "localhost", "::1"}


def _outbound_identity_ready(identity: dict[str, Any]) -> bool:
    if str(identity.get("mode") or "").strip().lower() != "centralized_alias":
        return False
    sending_domain = str(identity.get("sending_domain") or "").strip()
    reply_domain = str(identity.get("reply_domain") or "").strip()
    secret = str(identity.get("routing_secret") or "").strip()
    return all(
        value and "@" not in value and "/" not in value and " " not in value
        for value in (sending_domain, reply_domain)
    ) and len(secret) >= 24


def _reply_ingestion_ready(webhooks: dict[str, Any], apis: dict[str, Any], centralized_identity: bool) -> bool:
    if centralized_identity:
        return bool(apis.get("resend_key")) and len(str(webhooks.get("resend_secret") or "").strip()) >= 24
    return len(str(webhooks.get("inbound_email_secret") or "").strip()) >= 24


def _mail_transport_ready(
    apis: dict[str, Any],
    sender: dict[str, Any],
    sender_pool: dict[str, Any],
    smtp: dict[str, Any],
) -> bool:
    accounts = [account for account in sender_pool.get("accounts", []) if account.get("active", True)]
    providers = [str(account.get("provider") or sender.get("provider") or "resend").lower() for account in accounts]
    if not providers:
        providers = [str(sender.get("provider") or "resend").lower()]
    for provider in providers:
        if provider == "smtp":
            if not (smtp.get("host") and smtp.get("username") and smtp.get("password")):
                return False
        elif provider == "resend":
            if not apis.get("resend_key"):
                return False
        elif provider == "sendgrid":
            if not apis.get("sendgrid_key"):
                return False
        else:
            return False
    return True


def _admin_password_ready() -> bool:
    password = os.environ.get("SALESBOT_ADMIN_PASSWORD", "")
    return bool(password) and password != "admin123456" and len(password) >= 12
