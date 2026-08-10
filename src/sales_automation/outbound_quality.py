from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from .outreach_copy import contains_internal_outreach_data
from .outreach_guard import is_decision_title, is_low_value_title, is_sendable_email


POSITIVE_REPLY_LABELS = {
    "positive_interested",
    "positive_soft",
    "positive_referral",
}

_ICP_INDUSTRY_TERMS = {
    "automotive",
    "boutique",
    "consumer electronics",
    "dealer",
    "distributor",
    "fashion",
    "hospitality",
    "hotel",
    "jewelry",
    "jewellery",
    "luxury",
    "premium retail",
    "retail",
    "watch",
}
_ROLE_EMAIL_PREFIXES = {
    "admin",
    "contact",
    "hello",
    "info",
    "office",
    "sales",
    "support",
    "team",
}
_ALLOWED_TEMPLATE_FIELDS = {
    "company_name",
    "first_name",
    "sender_name",
    "sender_signature",
    "unsubscribe_url",
}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}|\[([A-Za-z_ ]+)\]")
_HYPE_PATTERNS = {
    "fake_urgency": re.compile(r"\b(act now|last chance|only two spots|limited time)\b", re.I),
    "unverifiable_return": re.compile(r"\b(guaranteed|200%\s*return|risk[- ]free|double your)\b", re.I),
}
_PEER_TO_PEER_PATTERNS = {
    "generic_flattery": re.compile(r"\b(esteemed company|outstanding reputation|world[- ]class company)\b", re.I),
    "template_cliche": re.compile(r"\b(hope this email finds you well|dear sir or madam|high quality and good price)\b", re.I),
    "salesy_pitch": re.compile(
        r"\b(we(?:'re| are) (?:the )?(?:world'?s )?(?:leading|premier|best[- ]in[- ]class)|"
        r"i(?:'m| am) reaching out to introduce|exclusive opportunity|don't miss (?:this|the) opportunity)\b",
        re.I,
    ),
}
_HIGH_FRICTION_CTA_RE = re.compile(
    r"\b(30[- ]?minute|half[- ]?hour|book (?:a )?(?:meeting|call)|schedule (?:a )?(?:meeting|call)|calendar invite)\b",
    re.I,
)
_SIGNATURE_MARKER_RE = re.compile(r"^(best regards|kind regards|regards|sincerely|thanks|thank you)[,!]?$", re.I)
_UNSUBSCRIBE_LINE_RE = re.compile(r"^unsubscribe\s*:", re.I)
_TARGET_COPY_WORD_RANGE = (70, 110)


def default_icp_profile() -> dict[str, Any]:
    return {
        "name": "VERTU channel partner ICP",
        "version": 1,
        "qualified_threshold": 70,
        "review_threshold": 50,
        "target_industries": sorted(_ICP_INDUSTRY_TERMS),
        "target_roles": [
            "owner",
            "founder",
            "partner",
            "ceo",
            "president",
            "director",
            "head",
            "vp",
            "commercial",
            "business development",
            "channel",
            "retail",
            "procurement",
        ],
        "disqualifiers": [
            "unsubscribed_or_complained",
            "bounced_email",
            "low_value_role",
            "missing_company_identity",
        ],
    }


def assess_icp(contact: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = {**default_icp_profile(), **(profile or {})}
    breakdown: dict[str, int] = {}
    reasons: list[str] = []
    disqualifiers: list[str] = []

    email = str(contact.get("email") or "").strip().lower()
    email_status = str(contact.get("email_status") or "").lower()
    if email_status == "valid" and is_sendable_email(email):
        breakdown["contactability"] = 20
        reasons.append("verified_personal_work_email")
    elif email and is_sendable_email(email):
        breakdown["contactability"] = 10
        reasons.append("personal_work_email_needs_verification")
    elif contact.get("phone") or contact.get("linkedin_url"):
        breakdown["contactability"] = 6
        reasons.append("alternate_contact_channel_only")
    else:
        breakdown["contactability"] = 0

    title = str(contact.get("job_title") or "").strip()
    if is_low_value_title(title):
        breakdown["role_authority"] = 0
        disqualifiers.append("low_value_role")
    elif is_decision_title(title):
        breakdown["role_authority"] = 25
        reasons.append("decision_role_match")
    elif title:
        breakdown["role_authority"] = 10
        reasons.append("role_present_but_authority_unclear")
    else:
        breakdown["role_authority"] = 0

    context = contact.get("source_context") if isinstance(contact.get("source_context"), dict) else {}
    account_text = " ".join(
        str(value or "").lower()
        for value in (
            contact.get("industry"),
            contact.get("company_name"),
            context.get("seed_category"),
            context.get("seed_reason"),
        )
    )
    target_terms = {str(item).lower() for item in profile.get("target_industries") or _ICP_INDUSTRY_TERMS}
    matched_terms = sorted(term for term in target_terms if term and term in account_text)
    if matched_terms:
        breakdown["account_fit"] = 25
        reasons.append(f"account_fit:{matched_terms[0]}")
    elif contact.get("industry") or context.get("seed_category"):
        breakdown["account_fit"] = 10
        reasons.append("account_category_needs_review")
    else:
        breakdown["account_fit"] = 4

    identity_points = 0
    if contact.get("company_name") or contact.get("company_domain"):
        identity_points += 7
    else:
        disqualifiers.append("missing_company_identity")
    if contact.get("first_name") or contact.get("last_name"):
        identity_points += 5
    confidence = _as_int(contact.get("identity_confidence"))
    if confidence >= 70 or str(contact.get("identity_status") or "").lower() in {"confirmed", "likely"}:
        identity_points += 3
        reasons.append("identity_supported")
    breakdown["identity_quality"] = min(15, identity_points)

    market_points = 0
    if contact.get("location"):
        market_points += 5
    if context.get("seed_reason"):
        market_points += 5
    if contact.get("linkedin_url") or context.get("source_url"):
        market_points += 5
    breakdown["market_evidence"] = min(15, market_points)

    status_text = " ".join(
        str(contact.get(key) or "").lower()
        for key in ("status", "disposition", "last_error", "email_status")
    )
    if any(token in status_text for token in ("unsubscribed", "complained", "blacklist")):
        disqualifiers.append("unsubscribed_or_complained")
    if "bounced" in status_text or email_status == "invalid":
        disqualifiers.append("bounced_email")

    score = max(0, min(100, sum(breakdown.values())))
    qualified_threshold = _as_int(profile.get("qualified_threshold"), 70)
    review_threshold = _as_int(profile.get("review_threshold"), 50)
    priority_threshold = min(95, qualified_threshold + 15)
    if disqualifiers:
        tier = "disqualified"
    elif score >= priority_threshold:
        tier = "priority"
    elif score >= qualified_threshold:
        tier = "qualified"
    elif score >= review_threshold:
        tier = "review"
    else:
        tier = "disqualified"
    confidence_score = min(
        100,
        35
        + 10 * sum(bool(contact.get(field)) for field in ("job_title", "industry", "location", "company_domain"))
        + (20 if context.get("seed_reason") else 0)
        + (5 if confidence >= 70 else 0),
    )
    return {
        "score": score,
        "tier": tier,
        "qualified": tier in {"priority", "qualified"},
        "confidence": confidence_score,
        "breakdown": breakdown,
        "reasons": reasons,
        "disqualifiers": list(dict.fromkeys(disqualifiers)),
        "profile_name": profile.get("name"),
        "profile_version": profile.get("version", 1),
    }


def score_lead_list(contacts: Iterable[dict[str, Any]], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = list(contacts)
    total = len(rows)
    if not total:
        return {
            "score": 0,
            "grade": "F",
            "total": 0,
            "sendable": 0,
            "qualified": 0,
            "dimensions": {},
            "issues": ["empty_list"],
            "actions": ["Import at least one lead before running the scorecard."],
            "sample_warning": "No leads to evaluate.",
        }

    emails = [str(row.get("email") or "").lower() for row in rows if row.get("email")]
    domains = [email.rsplit("@", 1)[-1] for email in emails if "@" in email]
    email_counts = Counter(emails)
    domain_counts = Counter(domains)
    assessments = [assess_icp(row, profile) for row in rows]
    valid = sum(
        str(row.get("email_status") or "").lower() == "valid" and is_sendable_email(row.get("email"))
        for row in rows
    )
    duplicate_emails = sum(count - 1 for count in email_counts.values() if count > 1)
    excessive_domains = sum(max(0, count - 5) for count in domain_counts.values())
    relevant_titles = sum(is_decision_title(row.get("job_title")) for row in rows)
    bad_titles = sum(is_low_value_title(row.get("job_title")) for row in rows)
    role_based = sum(
        bool(row.get("email"))
        and str(row.get("email")).split("@", 1)[0].lower() in _ROLE_EMAIL_PREFIXES
        for row in rows
    )
    complete_names = sum(bool(row.get("first_name")) and bool(row.get("last_name")) for row in rows)
    qualified = sum(item["qualified"] for item in assessments)
    average_icp = round(sum(item["score"] for item in assessments) / total)

    dimensions = {
        "email_verification": _percent(valid, total),
        "unique_emails": _percent(total - duplicate_emails, total),
        "domain_diversity": _percent(total - excessive_domains, total),
        "title_relevance": _percent(relevant_titles, total),
        "title_quality": _percent(total - bad_titles, total),
        "personal_email_density": _percent(total - role_based, total),
        "icp_fit": average_icp,
        "name_quality": _percent(complete_names, total),
    }
    weights = {
        "email_verification": 2,
        "unique_emails": 1,
        "domain_diversity": 1,
        "title_relevance": 1,
        "title_quality": 1,
        "personal_email_density": 1,
        "icp_fit": 2,
        "name_quality": 1,
    }
    score = round(sum(dimensions[key] * weights[key] for key in dimensions) / sum(weights.values()))
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    issues: list[str] = []
    actions: list[str] = []
    if dimensions["email_verification"] < 80:
        issues.append("low_verified_email_rate")
        actions.append("Prioritize email verification before sending.")
    if dimensions["title_relevance"] < 60:
        issues.append("weak_decision_maker_coverage")
        actions.append("Enrich or replace contacts without commercial decision authority.")
    if dimensions["icp_fit"] < 70:
        issues.append("weak_icp_fit")
        actions.append("Tighten industry, role and account criteria for the next sourcing batch.")
    if duplicate_emails:
        issues.append("duplicate_emails")
        actions.append("Remove duplicate recipient emails.")
    if role_based:
        issues.append("role_based_emails")
        actions.append("Keep generic company emails in review; do not auto-send.")
    return {
        "score": score,
        "grade": grade,
        "total": total,
        "sendable": valid,
        "qualified": qualified,
        "dimensions": dimensions,
        "issues": issues,
        "actions": actions,
        "sample_warning": "Directional only: fewer than 30 leads." if total < 30 else "",
    }


def review_email_copy(subject: str, body: str) -> dict[str, Any]:
    subject = str(subject or "").strip()
    body = str(body or "").strip()
    blocking: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    score = 100

    if not subject:
        blocking.append(_issue("missing_subject", "Add a specific subject line."))
        score -= 35
    elif len(subject) > 65:
        warnings.append(_issue("long_subject", "Shorten the subject to about 30-60 characters."))
        score -= 8
    prospect_word_count = prospect_copy_word_count(body)
    target_min, target_max = _TARGET_COPY_WORD_RANGE
    if len(body) < 80:
        blocking.append(_issue("body_too_short", "Add enough context, value and one clear question."))
        score -= 30
    elif len(body) > 1600:
        warnings.append(_issue("body_too_long", "Shorten the first-touch email to make it easier to scan."))
        score -= 12
    if prospect_word_count < target_min:
        warnings.append(
            _issue(
                "body_below_target_words",
                f"Use {target_min}-{target_max} prospect-facing words before the signature; this draft has {prospect_word_count}.",
            )
        )
        score -= 12
    elif prospect_word_count > target_max:
        warnings.append(
            _issue(
                "body_above_target_words",
                f"Use {target_min}-{target_max} prospect-facing words before the signature; this draft has {prospect_word_count}.",
            )
        )
        score -= 12

    unresolved = []
    for match in _PLACEHOLDER_RE.finditer(f"{subject}\n{body}"):
        field = (match.group(1) or match.group(2) or "").strip()
        if field not in _ALLOWED_TEMPLATE_FIELDS:
            unresolved.append(field)
    if unresolved:
        blocking.append(_issue("unresolved_placeholders", f"Resolve placeholders: {', '.join(sorted(set(unresolved)))}."))
        score -= 35
    if contains_internal_outreach_data(f"{subject}\n{body}"):
        blocking.append(_issue("internal_data_exposed", "Remove CRM fields, scores, verification notes and source IDs."))
        score -= 50

    visible_copy = f"{subject}\n{body}"
    for code, pattern in _HYPE_PATTERNS.items():
        if pattern.search(visible_copy):
            issue = _issue(code, _copy_recommendation(code))
            if code == "unverifiable_return":
                blocking.append(issue)
            else:
                warnings.append(issue)
            score -= 10
    peer_to_peer_issues: list[str] = []
    for code, pattern in _PEER_TO_PEER_PATTERNS.items():
        if pattern.search(visible_copy):
            peer_to_peer_issues.append(code)
            warnings.append(_issue(code, _copy_recommendation(code)))
            score -= 12
    if len(re.findall(r"!", f"{subject}\n{body}")) > 1:
        warnings.append(_issue("excessive_exclamation", "Use calm punctuation and remove repeated exclamation marks."))
        score -= 6
    if re.search(r"\b[A-Z]{5,}\b", subject):
        warnings.append(_issue("subject_all_caps", "Avoid all-caps words in the subject."))
        score -= 6
    question_count = body.count("?")
    if question_count == 0:
        warnings.append(_issue("missing_question", "End with one low-friction qualification question."))
        score -= 8
    elif question_count > 1:
        warnings.append(_issue("too_many_questions", "Keep one primary call to action."))
        score -= 5
    high_friction_cta = bool(_HIGH_FRICTION_CTA_RE.search(body))
    if high_friction_cta:
        warnings.append(_issue("high_friction_cta", "For a first touch, ask permission to send a short local-market outline instead of requesting a meeting."))
        score -= 8
    if "unsubscribe" not in body.lower() and "{{unsubscribe_url}}" not in body:
        warnings.append(_issue("missing_unsubscribe", "Keep the unsubscribe line in the final message."))
        score -= 5

    score = max(0, score)
    status = "blocked" if blocking else "ready" if score >= 80 else "revise"
    return {
        "score": score,
        "grade": "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F",
        "status": status,
        "blocking_issues": blocking,
        "warnings": warnings,
        "rules": {
            "peer_to_peer": not peer_to_peer_issues,
            "prospect_word_count": prospect_word_count,
            "target_word_range": list(_TARGET_COPY_WORD_RANGE),
            "word_count_in_range": target_min <= prospect_word_count <= target_max,
            "cta_count": question_count,
            "single_low_friction_cta": question_count == 1 and not high_friction_cta,
        },
        "summary": (
            "Ready for human approval."
            if status == "ready"
            else "Must be corrected before approval."
            if status == "blocked"
            else "Sendable after reviewing the warnings."
        ),
    }


def prospect_copy_word_count(body: str) -> int:
    """Count only the prospect-facing message, excluding signature and unsubscribe text."""
    visible_lines: list[str] = []
    for raw_line in str(body or "").splitlines():
        line = raw_line.strip()
        if _SIGNATURE_MARKER_RE.match(line):
            break
        if _UNSUBSCRIBE_LINE_RE.match(line):
            continue
        visible_lines.append(line)
    return len(re.findall(r"[\w]+(?:[’'-][\w]+)*", " ".join(visible_lines), flags=re.UNICODE))


def classify_reply(subject: str | None, body: str | None) -> dict[str, Any]:
    text = _reply_text(subject, body)
    rules = [
        ("bounce", 0, False, r"\b(undeliverable|delivery failed|mailbox unavailable|user unknown|address not found)\b"),
        ("ooo", 0, False, r"\b(out of office|automatic reply|auto[- ]?reply|away from the office|on leave)\b"),
        ("unsubscribe", 0, False, r"\b(unsubscribe|remove me|stop emailing|do not contact)\b"),
        ("negative_hostile", 0, False, r"\b(spam|reported|harassment|never contact|leave me alone)\b"),
        ("negative_notfit", 5, False, r"\b(not interested|not relevant|not a fit|no need|we do not)\b"),
        ("negative_notnow", 20, False, r"\b(not now|maybe later|next quarter|next year|circle back|no budget)\b"),
        ("positive_referral", 85, True, r"\b(contact|reach out to|speak with|forwarded to|copied|cc'?d)\b.{0,80}\b(colleague|manager|director|team|person)\b"),
        ("positive_interested", 95, True, r"\b(interested|let'?s talk|book a call|schedule|meeting|proposal|quotation|quote|pricing|price list|send details)\b"),
        ("positive_soft", 75, True, r"\b(tell me more|more information|learn more|sounds good|could you share|please send)\b"),
        ("neutral_question", 55, True, r"\b(who are you|what is|how does|which market|where are|can you explain)\b|\?"),
    ]
    for label, score, should_advance, pattern in rules:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return {
                "label": label,
                "positive": label in POSITIVE_REPLY_LABELS,
                "score": score,
                "should_advance": should_advance,
                "reason": match.group(0)[:160],
            }
    return {
        "label": "other",
        "positive": False,
        "score": 40,
        "should_advance": False,
        "reason": "No reliable intent signal detected.",
    }


def summarize_experiment(variants: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for item in variants:
        sent = max(0, _as_int(item.get("sent")))
        positive = max(0, _as_int(item.get("positive_replies")))
        replied = max(0, _as_int(item.get("replies")))
        rows.append(
            {
                "name": str(item.get("name") or item.get("variant") or f"Variant {len(rows) + 1}"),
                "sent": sent,
                "delivered": max(0, _as_int(item.get("delivered"))),
                "opened": max(0, _as_int(item.get("opened"))),
                "replies": replied,
                "positive_replies": positive,
                "bounced": max(0, _as_int(item.get("bounced"))),
                "unsubscribed": max(0, _as_int(item.get("unsubscribed"))),
                "reply_rate": _rate(replied, sent),
                "positive_reply_rate": _rate(positive, sent),
            }
        )
    if not rows:
        return {"variants": [], "winner": None, "status": "no_data", "recommendation": "No experiment data yet."}
    enough_sample = len(rows) >= 2 and all(row["sent"] >= 100 for row in rows)
    ordered = sorted(rows, key=lambda row: (row["positive_reply_rate"], row["positive_replies"]), reverse=True)
    winner = ordered[0]["name"] if enough_sample and ordered[0]["positive_reply_rate"] > ordered[1]["positive_reply_rate"] else None
    return {
        "variants": rows,
        "winner": winner,
        "status": "decision_ready" if winner else "collecting",
        "recommendation": (
            f"Keep {winner}; it has the highest positive-reply rate."
            if winner
            else "Keep the test single-variable and collect at least 100 sends per variant before choosing a winner."
        ),
        "sample_warning": "" if enough_sample else "Sample size is not yet decision-grade.",
    }


def calibration_summary(feedback: Iterable[dict[str, Any]], *, current_threshold: int = 70) -> dict[str, Any]:
    rows = list(feedback)
    false_positive = sum(bool(row.get("predicted_qualified")) and not bool(row.get("expected_qualified")) for row in rows)
    false_negative = sum(not bool(row.get("predicted_qualified")) and bool(row.get("expected_qualified")) for row in rows)
    correct = len(rows) - false_positive - false_negative
    if len(rows) < 10:
        recommendation = "Collect at least 10 reviewed examples before changing the ICP threshold."
        proposed = current_threshold
    elif false_positive > false_negative:
        proposed = min(90, current_threshold + 5)
        recommendation = "Tighten the ICP threshold by 5 points and review the repeated false-positive traits."
    elif false_negative > false_positive:
        proposed = max(40, current_threshold - 5)
        recommendation = "Relax the ICP threshold by 5 points and review the missed positive traits."
    else:
        proposed = current_threshold
        recommendation = "Keep the current threshold; reviewed errors are balanced."
    return {
        "reviewed": len(rows),
        "correct": correct,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "accuracy": _percent(correct, len(rows)) if rows else 0,
        "current_threshold": current_threshold,
        "proposed_threshold": proposed,
        "recommendation": recommendation,
    }


def _reply_text(subject: str | None, body: str | None) -> str:
    text = f"{subject or ''}\n{body or ''}".lower()
    for marker in ("\non ", "\nfrom:", "\n-----original message-----"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return re.sub(r"\s+", " ", text).strip()[:5000]


def _issue(code: str, recommendation: str) -> dict[str, str]:
    return {"code": code, "recommendation": recommendation}


def _copy_recommendation(code: str) -> str:
    return {
        "fake_urgency": "Replace artificial urgency with a factual timing reason.",
        "unverifiable_return": "Remove guarantees and unsupported return claims.",
        "generic_flattery": "Replace generic praise with one verifiable account observation.",
        "template_cliche": "Use a direct, specific opening tied to the recipient.",
        "salesy_pitch": "Write as a commercial peer: describe the local channel opportunity without a promotional claim.",
    }[code]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _percent(numerator: int, denominator: int) -> int:
    return round(100 * numerator / denominator) if denominator else 0


def _rate(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0
