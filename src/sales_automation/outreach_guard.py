from __future__ import annotations

import json
import random
import re
import time
from typing import Any

from .clients import is_full_email


ROLE_BASED_PREFIXES = {
    "admin",
    "billing",
    "contact",
    "hello",
    "help",
    "info",
    "office",
    "press",
    "sales",
    "support",
    "team",
}


def is_sendable_email(value: str | None) -> bool:
    if not value or not is_full_email(value):
        return False
    local = value.split("@", 1)[0].lower()
    return local not in ROLE_BASED_PREFIXES and "*" not in value


def validate_email_body(subject: str, text: str, *, min_chars: int = 80) -> None:
    if not subject.strip():
        raise ValueError("Email subject is empty")
    stripped = text.strip()
    if len(stripped) < min_chars:
        raise ValueError(f"Email body too short: {len(stripped)} chars")
    if re.search(r"\{\{[^}]+\}\}|\[[A-Za-z_ ]+\]", stripped):
        raise ValueError("Email body still contains unresolved placeholders")


def send_delay_seconds(config: Any) -> float:
    outreach = getattr(config, "raw", {}).get("outreach", {})
    base = float(outreach.get("send_delay_seconds") or 0)
    jitter = float(outreach.get("send_jitter_seconds") or 0)
    if base <= 0 and jitter <= 0:
        return 0.0
    return max(0.0, base + random.uniform(0, max(0.0, jitter)))


def sleep_between_sends(config: Any) -> None:
    delay = send_delay_seconds(config)
    if delay > 0:
        time.sleep(delay)


def classify_delivery_failure(event_type: str, payload: dict[str, Any]) -> str | None:
    normalized = str(event_type or "").lower()
    text = json.dumps(payload, ensure_ascii=False, default=str).lower()
    if "complain" in normalized or "complain" in text:
        return "complained"
    if "unsubscribe" in normalized or "unsubscribe" in text:
        return "unsubscribed"
    if "suppress" in normalized or "suppressed" in text:
        return "bounced: suppressed"
    if "address not found" in text or "user unknown" in text or "mailbox unavailable" in text:
        return "bounced: address_not_found"
    if "mailbox full" in text or "quota exceeded" in text:
        return "bounced: mailbox_full"
    if "domain not found" in text or "dns" in text or "no mx" in text:
        return "bounced: domain_not_found"
    if "blocked" in text or "spam" in text or "reject" in text or "denied" in text:
        return "bounced: provider_rejected"
    if normalized in {"bounced", "bounce", "failed"} or "bounce" in normalized or "fail" in normalized:
        return "bounced: unknown"
    return None


def annotate_delivery_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    reason = classify_delivery_failure(event_type, payload)
    if not reason:
        return payload
    enriched = dict(payload)
    enriched.setdefault("delivery_reason", reason)
    return enriched
