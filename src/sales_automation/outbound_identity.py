from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from email.utils import getaddresses
from typing import Any, Iterable

from .config import AppConfig


CENTRALIZED_ALIAS_MODE = "centralized_alias"
_LOCALPART_RE = re.compile(r"[^a-z0-9._-]+")


def centralized_identity_enabled(config: AppConfig) -> bool:
    return str(config.raw.get("outbound_identity", {}).get("mode") or "").strip().lower() == CENTRALIZED_ALIAS_MODE


def outbound_sender(config: AppConfig, user: dict[str, Any] | None, transport_sender: dict[str, Any]) -> dict[str, Any]:
    """Return the customer-visible sender while retaining transport account metadata."""

    sender = dict(transport_sender)
    sender["name"] = _sender_display_name(user, str(sender.get("name") or "VERTU"))
    if not centralized_identity_enabled(config):
        return sender
    identity = config.raw.get("outbound_identity", {})
    domain = _clean_domain(identity.get("sending_domain"))
    if not domain:
        raise RuntimeError("OUTBOUND_SENDING_DOMAIN is required for centralized_alias mode")
    localpart = sender_alias_localpart(user, fallback=identity.get("default_localpart"))
    sender["transport_email"] = transport_sender.get("email")
    smtp_config = config.raw.get("smtp", {})
    smtp_alias_allowed = str(smtp_config.get("allow_from_alias") or "").strip().lower() in {"1", "true", "yes", "on"}
    if str(transport_sender.get("provider") or "").lower() == "smtp" and not smtp_alias_allowed:
        sender["email"] = transport_sender.get("email")
    else:
        sender["email"] = f"{localpart}@{domain}"
    return sender


def sender_alias_localpart(user: dict[str, Any] | None, *, fallback: Any = None) -> str:
    configured = str((user or {}).get("sender_alias_localpart") or "").strip()
    username = str((user or {}).get("username") or "").strip()
    candidate = configured or username or str(fallback or "").strip()
    ascii_value = unicodedata.normalize("NFKD", candidate).encode("ascii", "ignore").decode("ascii").lower()
    localpart = _LOCALPART_RE.sub("-", ascii_value).strip(".-_")[:48]
    if localpart:
        return localpart
    user_id = int((user or {}).get("id") or 0)
    return f"sales-{user_id}" if user_id else "partnerships"


def signed_reply_address(
    config: AppConfig,
    *,
    contact_id: int,
    user_id: int | None,
    sequence_step: int,
) -> str | None:
    if not centralized_identity_enabled(config):
        return None
    identity = config.raw.get("outbound_identity", {})
    domain = _clean_domain(identity.get("reply_domain"))
    secret = str(identity.get("routing_secret") or "").strip()
    if not domain:
        raise RuntimeError("OUTBOUND_REPLY_DOMAIN is required for centralized_alias mode")
    if len(secret) < 24:
        raise RuntimeError("REPLY_ROUTING_SECRET must contain at least 24 characters")
    prefix = sender_alias_localpart(None, fallback=identity.get("reply_localpart") or "reply")
    payload = f"v1.{int(contact_id)}.{int(user_id or 0)}.{int(sequence_step)}"
    signature = _route_signature(secret, domain, payload)
    return f"{prefix}+{payload}.{signature}@{domain}"


def parse_signed_reply_route(config: AppConfig, recipients: Any) -> dict[str, Any] | None:
    if not centralized_identity_enabled(config):
        return None
    identity = config.raw.get("outbound_identity", {})
    domain = _clean_domain(identity.get("reply_domain"))
    secret = str(identity.get("routing_secret") or "").strip()
    if not domain or len(secret) < 24:
        return None
    prefix = sender_alias_localpart(None, fallback=identity.get("reply_localpart") or "reply")
    marker = f"{prefix}+"
    for address in _recipient_addresses(recipients):
        localpart, separator, recipient_domain = address.lower().rpartition("@")
        if not separator or recipient_domain != domain or not localpart.startswith(marker):
            continue
        parts = localpart[len(marker) :].split(".")
        if len(parts) != 5 or parts[0] != "v1":
            continue
        version, contact_text, user_text, step_text, supplied_signature = parts
        try:
            contact_id = int(contact_text)
            user_id = int(user_text)
            sequence_step = int(step_text)
        except ValueError:
            continue
        if contact_id <= 0 or user_id < 0 or sequence_step < 0:
            continue
        payload = f"{version}.{contact_id}.{user_id}.{sequence_step}"
        expected = _route_signature(secret, domain, payload)
        if not hmac.compare_digest(supplied_signature, expected):
            continue
        return {
            "contact_id": contact_id,
            "user_id": user_id or None,
            "sequence_step": sequence_step,
            "address": address.lower(),
        }
    return None


def _route_signature(secret: str, domain: str, payload: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), f"{domain}|{payload}".encode("utf-8"), hashlib.sha256).digest()
    return digest.hex()[:24]


def _recipient_addresses(value: Any) -> Iterable[str]:
    raw: list[str] = []
    _flatten_recipients(value, raw)
    for _, address in getaddresses(raw):
        cleaned = address.strip()
        if cleaned:
            yield cleaned


def _flatten_recipients(value: Any, output: list[str]) -> None:
    if isinstance(value, str):
        output.append(value)
        return
    if isinstance(value, dict):
        email = value.get("email") or value.get("address")
        if email:
            output.append(str(email))
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _flatten_recipients(item, output)


def _clean_domain(value: Any) -> str:
    domain = str(value or "").strip().lower().rstrip(".")
    if not domain or "/" in domain or "@" in domain or " " in domain:
        return ""
    return domain


def _sender_display_name(user: dict[str, Any] | None, fallback: str) -> str:
    value = str((user or {}).get("display_name") or (user or {}).get("username") or fallback or "VERTU").strip()
    return value or "VERTU"


__all__ = [
    "CENTRALIZED_ALIAS_MODE",
    "centralized_identity_enabled",
    "outbound_sender",
    "parse_signed_reply_route",
    "sender_alias_localpart",
    "signed_reply_address",
]
