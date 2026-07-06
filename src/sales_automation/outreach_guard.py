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

DECISION_TITLE_KEYWORDS = {
    "bd",
    "business development",
    "ceo",
    "chairman",
    "channel",
    "commercial",
    "co-founder",
    "cofounder",
    "director",
    "founder",
    "head",
    "managing director",
    "owner",
    "partner",
    "partnership",
    "president",
    "principal",
    "procurement",
    "retail",
    "sales director",
    "vp",
}

LOW_VALUE_TITLE_KEYWORDS = {
    "assistant",
    "customer service",
    "intern",
    "reception",
    "receptionist",
    "support",
}


def is_sendable_email(value: str | None) -> bool:
    if not value or not is_full_email(value):
        return False
    local = value.split("@", 1)[0].lower()
    return local not in ROLE_BASED_PREFIXES and "*" not in value


def lead_quality_score(contact: dict[str, Any]) -> int:
    explicit = contact.get("lead_score")
    if explicit not in {None, ""}:
        try:
            return max(0, min(100, int(explicit)))
        except (TypeError, ValueError):
            pass
    score = 0
    if is_sendable_email(contact.get("email")) and contact.get("email_status") == "valid":
        score += 30
    if contact.get("company_name") or contact.get("company_domain"):
        score += 20
    if is_decision_title(contact.get("job_title")):
        score += 20
    if contact.get("first_name") or contact.get("last_name"):
        score += 10
    if contact.get("location") or contact.get("industry"):
        score += 10
    context = contact.get("source_context") if isinstance(contact.get("source_context"), dict) else {}
    if context.get("seed_reason") or context.get("seed_category"):
        score += 10
    return max(0, min(100, score))


def is_decision_title(title: str | None) -> bool:
    normalized = str(title or "").strip().lower()
    return bool(normalized) and any(keyword in normalized for keyword in DECISION_TITLE_KEYWORDS)


def is_low_value_title(title: str | None) -> bool:
    normalized = str(title or "").strip().lower()
    return bool(normalized) and any(keyword in normalized for keyword in LOW_VALUE_TITLE_KEYWORDS)


def send_readiness(contact: dict[str, Any], *, min_score: int = 50) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    email = str(contact.get("email") or "")
    if contact.get("email_status") != "valid":
        reasons.append("email_not_verified")
    if not is_sendable_email(email):
        reasons.append("email_not_personal_work")
    confidence = contact.get("email_confidence")
    if confidence not in {None, ""}:
        try:
            if int(confidence) < 70:
                warnings.append("email_confidence_below_70")
        except (TypeError, ValueError):
            pass
    source = str(contact.get("email_source") or "").lower()
    if source in {"public_website", "company_seed", "manual_company_seed"}:
        warnings.append("email_source_needs_review")
    if is_low_value_title(contact.get("job_title")):
        reasons.append("low_value_title")
    elif contact.get("job_title") and not is_decision_title(contact.get("job_title")):
        warnings.append("title_not_decision_role")
    score = lead_quality_score(contact)
    if score < min_score:
        reasons.append("lead_score_below_threshold")
    return {
        "ok": not reasons,
        "score": score,
        "reasons": list(dict.fromkeys(reasons)),
        "warnings": list(dict.fromkeys(warnings)),
        "tier": "sendable" if not reasons else "review",
    }


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
