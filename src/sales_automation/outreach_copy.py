from __future__ import annotations

import re
from typing import Any


_INTERNAL_MARKERS = re.compile(
    r"("
    r"触达优先级|核实状态|是否跟进|是否回复|跟进进度|跟进人|客户来源|"
    r"联系人\s*[:：]|线索评分|客户评分|"
    r"lead[_\s-]?score|score[_\s-]?breakdown|verification[_\s-]?status|"
    r"follow[_\s-]?up[_\s-]?status|source[_\s-]?context|seed[_\s-]?reason|"
    r"priority\s*[:=]\s*p[0-3]"
    r")",
    re.IGNORECASE,
)
_ANALYTICS_PARENTHETICAL = re.compile(
    r"\([^)]*(?:current signals?|openings reported|confidence\s*\d+|score\s*\d+)[^)]*\)",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
_CJK = re.compile(r"[\u3400-\u9fff]")


def contains_internal_outreach_data(value: Any) -> bool:
    return bool(_INTERNAL_MARKERS.search(str(value or "")))


def customer_visible_source_context(contact: dict[str, Any]) -> dict[str, str]:
    raw = contact.get("source_context")
    if not isinstance(raw, dict):
        return {}
    visible: dict[str, str] = {}
    for key in ("seed_category", "seed_location"):
        value = _clean_public_text(raw.get(key), max_chars=120)
        if value and not _CJK.search(value):
            visible[key] = value
    for key in ("hiring_signal_summary", "public_signal", "seed_reason"):
        value = _clean_public_text(raw.get(key), max_chars=240)
        if value:
            visible["public_signal" if key != "seed_reason" else "seed_reason"] = value
            break
    return visible


def customer_visible_contact(contact: dict[str, Any]) -> dict[str, Any]:
    visible = {
        **contact,
        "source_context": customer_visible_source_context(contact),
        "profile_insights": {},
    }
    visible["industry"] = _clean_english_label(contact.get("industry")) or "premium retail and distribution"
    return visible


def clean_public_research_item(item: Any) -> dict[str, str] | None:
    if not isinstance(item, dict):
        return None
    title = _clean_public_text(item.get("title"), max_chars=180)
    snippet = _clean_public_text(item.get("snippet"), max_chars=300)
    if not title and not snippet:
        return None
    result = {
        "title": title,
        "snippet": snippet,
        "published_at": _clean_public_text(item.get("published_at"), max_chars=40),
        "url": str(item.get("url") or "").strip()[:500],
    }
    return {key: value for key, value in result.items() if value}


def _clean_public_text(value: Any, *, max_chars: int) -> str:
    text = _WHITESPACE.sub(" ", str(value or "")).strip()
    if not text or contains_internal_outreach_data(text):
        return ""
    text = _ANALYTICS_PARENTHETICAL.sub("", text)
    text = text.strip(" |;,-")
    return text[:max_chars].rstrip() if text else ""


def _clean_english_label(value: Any) -> str:
    text = _clean_public_text(value, max_chars=120)
    if not text:
        return ""
    if not _CJK.search(text):
        return text
    for part in re.split(r"[/|;,]", text):
        candidate = part.strip()
        if candidate and not _CJK.search(candidate) and re.search(r"[A-Za-z]", candidate):
            return candidate
    return ""


__all__ = [
    "clean_public_research_item",
    "contains_internal_outreach_data",
    "customer_visible_contact",
    "customer_visible_source_context",
]
