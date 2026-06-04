from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .config import AppConfig


def readiness(config: AppConfig) -> dict[str, Any]:
    apis = config.apis
    sender = config.sender
    llm = config.raw.get("llm", {})
    app = config.raw.get("app", {})
    checks = [
        _check("database", bool(config.database.get("host") and config.database.get("dbname")), "Database config is present"),
        _check("lead_source", bool(apis.get("prospeo_key") or apis.get("ninjapear_key")), "Prospeo or NinjaPear key is required for automated lead sourcing"),
        _check("enrichment", bool(apis.get("hunter_key") or apis.get("prospeo_key") or apis.get("ninjapear_key")), "Hunter, Prospeo, or NinjaPear key is required for email enrichment"),
        _check("social_enrichment", bool(apis.get("peopledb_key") or apis.get("pdl_key")), "PeopleDB or People Data Labs key is optional for social profile enrichment"),
        _check("resend", bool(apis.get("resend_key")), "Resend key is required for real email sending"),
        _check("sender_email", _valid_sender(sender.get("email")), "Sender email must be a verified-domain address, not a placeholder"),
        _check("dry_run", sender.get("dry_run") is False, "Set sender.dry_run=false only after sender domain is verified"),
        _check("public_url", _public_base_url_ready(app.get("public_base_url")), "Use a public HTTPS PUBLIC_BASE_URL for unsubscribe links, tracking pixels, and webhooks"),
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
