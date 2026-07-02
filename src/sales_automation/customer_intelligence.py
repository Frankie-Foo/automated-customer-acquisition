from __future__ import annotations

from typing import Any


HIGH_VALUE_ROLE_KEYWORDS = (
    "founder",
    "owner",
    "ceo",
    "co-founder",
    "partner",
    "director",
    "head",
    "vp",
    "president",
    "principal",
    "buyer",
    "procurement",
    "general manager",
)

VERTU_FIT_KEYWORDS = (
    "luxury",
    "premium",
    "watch",
    "jewelry",
    "jewellery",
    "fashion",
    "boutique",
    "concierge",
    "supercar",
    "automotive",
    "dealer",
    "retail",
    "reseller",
    "distributor",
    "second hand",
    "pre-owned",
    "used",
)

LOW_VALUE_ROLE_KEYWORDS = (
    "intern",
    "assistant",
    "student",
    "recruiter",
    "hr",
    "talent",
)

SABCD_STAGE_WEIGHTS = {
    "S": 35,
    "A": 28,
    "B": 20,
    "C": 8,
    "D": 0,
}


def build_customer_profile(contact: dict[str, Any]) -> dict[str, Any]:
    score_parts = score_customer(contact)
    icp_score = max(0, min(100, int(sum(score_parts.values()))))
    next_action = next_best_action(contact, icp_score)
    return {
        "summary": profile_summary(contact, icp_score, next_action),
        "persona": persona_label(contact),
        "icp_fit_score": icp_score,
        "fit_score_breakdown": score_parts,
        "intent_level": intent_level(contact, icp_score),
        "buying_stage": contact.get("lifecycle_stage") or "lead",
        "interests": inferred_interests(contact),
        "pain_points": inferred_pain_points(contact),
        "objections": inferred_objections(contact),
        "risks": inferred_risks(contact),
        "next_action": next_action,
        "why_now": why_now(contact, icp_score),
        "email_framework": outreach_framework(contact),
    }


def score_customer(contact: dict[str, Any]) -> dict[str, int]:
    text = _joined_text(
        contact.get("job_title"),
        contact.get("industry"),
        contact.get("company_name"),
        contact.get("company_domain"),
        _source_context(contact).get("seed_category"),
        _source_context(contact).get("seed_reason"),
    )
    role = str(contact.get("job_title") or "").lower()
    source_context = _source_context(contact)
    parts = {
        "role_authority": 0,
        "vertical_fit": 0,
        "contactability": 0,
        "account_context": 0,
        "engagement_stage": 0,
        "data_quality": 0,
        "risk_penalty": 0,
    }
    if any(keyword in role for keyword in HIGH_VALUE_ROLE_KEYWORDS):
        parts["role_authority"] = 22
    elif role:
        parts["role_authority"] = 10
    if any(keyword in role for keyword in LOW_VALUE_ROLE_KEYWORDS):
        parts["role_authority"] = max(0, parts["role_authority"] - 12)

    hits = sum(1 for keyword in VERTU_FIT_KEYWORDS if keyword in text)
    parts["vertical_fit"] = min(24, hits * 6)
    if source_context.get("seed_category") or source_context.get("seed_reason"):
        parts["account_context"] = 14
    if contact.get("email_status") == "valid" and contact.get("email"):
        parts["contactability"] += 16
    elif contact.get("email_candidates"):
        parts["contactability"] += 7
    if contact.get("phone") or contact.get("phone_candidates"):
        parts["contactability"] += 5
    sabcd = str(contact.get("sabcd_stage") or "D").upper()
    parts["engagement_stage"] = SABCD_STAGE_WEIGHTS.get(sabcd, 0)
    if contact.get("company_domain"):
        parts["data_quality"] += 5
    if contact.get("linkedin_url"):
        parts["data_quality"] += 5
    if contact.get("location"):
        parts["data_quality"] += 4
    if contact.get("status") in {"bounced", "unsubscribed"} or contact.get("disposition") in {"abandoned", "lost"}:
        parts["risk_penalty"] = -80
    return parts


def outreach_framework(contact: dict[str, Any]) -> dict[str, str]:
    company = contact.get("company_name") or "your company"
    role = contact.get("job_title") or "your role"
    source_context = _source_context(contact)
    business_match = source_context.get("seed_reason") or source_context.get("seed_category") or contact.get("industry") or ""
    return {
        "intent": f"Briefly ask whether {company} is open to a practical channel cooperation conversation.",
        "business_match": business_match or f"Reference the recipient's work as {role} at {company}.",
        "our_value": "Position Vertu as a premium mobile/luxury technology brand suitable for selective high-end retail or distributor channels.",
        "low_barrier_ask": "Ask for a short reply or a 15-minute exploratory call, not a heavy proposal immediately.",
        "close": "Keep the ending direct, polite, and easy to say yes/no to.",
    }


def next_best_action(contact: dict[str, Any], score: int | None = None) -> str:
    status = contact.get("status")
    step = int(contact.get("sequence_step") or 0)
    lifecycle = contact.get("lifecycle_stage") or "lead"
    disposition = contact.get("disposition") or "active"
    score = int(score if score is not None else sum(score_customer(contact).values()))
    if disposition in {"abandoned", "lost"} or status in {"bounced", "unsubscribed"}:
        return "Close the loop and stop outreach; keep the record for suppression and future reporting."
    if lifecycle in {"business_plan", "trial_order", "agency_agreement", "store_creation"}:
        return "Prepare stage-specific materials and record the next decision point in the customer workspace."
    if status == "replied" or lifecycle in {"replied", "conversation", "meeting"}:
        return "Move from outreach to sales conversation: collect missing account facts, confirm decision process, and schedule the next discussion."
    if score >= 70 and status in {"new", "enriched"}:
        return "Prioritize this account: generate a personalized five-part first-touch email and send after human review."
    if step == 1:
        return "If opened but not replied, send a short second touch with one sharper business reason."
    if step == 2:
        return "Send the third touch only if the account still looks relevant; otherwise move to the waiting pool."
    if step >= 3:
        return "No more automatic chasing; move no-reply accounts to the waiting pool or abandon low-fit accounts."
    return "Complete missing email/contact data, score the account, then decide whether to personalize and send."


def profile_summary(contact: dict[str, Any], score: int, next_action: str) -> str:
    company = contact.get("company_name") or "Unknown company"
    role = contact.get("job_title") or "Unknown role"
    sabcd = contact.get("sabcd_stage") or "D"
    return f"{company} / {role}; SABCD={sabcd}; fit score={score}. Next: {next_action}"


def persona_label(contact: dict[str, Any]) -> str:
    bits = [contact.get("company_name"), contact.get("job_title")]
    source_context = _source_context(contact)
    if source_context.get("seed_category"):
        bits.append(source_context["seed_category"])
    if contact.get("location"):
        bits.append(contact["location"])
    return " / ".join(str(item) for item in bits if item) or "Unclassified account"


def intent_level(contact: dict[str, Any], score: int) -> str:
    if contact.get("status") == "replied" or contact.get("lifecycle_stage") in {"conversation", "meeting", "business_plan", "trial_order", "agency_agreement", "store_creation"}:
        return "high"
    if score >= 70:
        return "medium"
    if score >= 45:
        return "low"
    return "unknown"


def inferred_interests(contact: dict[str, Any]) -> list[str]:
    interests: list[str] = []
    text = _joined_text(contact.get("industry"), _source_context(contact).get("seed_reason"), _source_context(contact).get("seed_category"))
    for keyword in VERTU_FIT_KEYWORDS:
        if keyword in text and keyword not in interests:
            interests.append(keyword)
    return interests[:5]


def inferred_pain_points(contact: dict[str, Any]) -> list[str]:
    points = ["Needs differentiated premium products and a selective channel story."]
    if contact.get("location"):
        points.append("Local market entry requires clear margin, inventory, service, and rollout logic.")
    if not contact.get("email"):
        points.append("Contact data is incomplete; verify a personal work email before outreach.")
    return points[:5]


def inferred_objections(contact: dict[str, Any]) -> list[str]:
    objections = ["May not immediately understand Vertu's fit with their existing assortment."]
    if int(contact.get("sequence_step") or 0) > 0 and contact.get("status") != "replied":
        objections.append("Previous outreach did not create a reply yet.")
    return objections


def inferred_risks(contact: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    if contact.get("status") in {"bounced", "unsubscribed"}:
        risks.append("Do not contact: bounced or unsubscribed.")
    if contact.get("email") and "*" in str(contact.get("email")):
        risks.append("Email is masked and must not be sent.")
    if not contact.get("company_domain"):
        risks.append("Company domain is missing; account research quality is limited.")
    if not contact.get("job_title"):
        risks.append("Contact role is missing; authority is uncertain.")
    return risks


def why_now(contact: dict[str, Any], score: int) -> str:
    source_context = _source_context(contact)
    if source_context.get("seed_reason"):
        return source_context["seed_reason"][:260]
    if score >= 70:
        return "The account has enough role, vertical, and contactability signals to justify prioritized outreach."
    return "Current evidence is limited; enrich the account before spending high-effort sales time."


def _source_context(contact: dict[str, Any]) -> dict[str, str]:
    context = contact.get("source_context")
    if not isinstance(context, dict):
        return {}
    return {str(key): str(value).strip() for key, value in context.items() if str(value or "").strip()}


def _joined_text(*items: Any) -> str:
    return " ".join(str(item or "").lower() for item in items)
