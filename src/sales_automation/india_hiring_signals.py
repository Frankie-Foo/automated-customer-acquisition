from __future__ import annotations

from typing import Any

from .logging_utils import log
from .regional_sourcing import detect_regional_profile
from .southeast_asia_hiring_signals import (
    HiringPlatform,
    PublicSearchClient,
    build_platform_hiring_query,
    detect_sales_vertical,
    parse_platform_hiring_signals,
    score_hiring_signals,
    summarize_hiring_signals,
)


INDIA_DECISION_ROLE_TERMS = (
    "business head",
    "category head",
    "channel sales head",
    "commercial director",
    "country head",
    "franchise development head",
    "general manager",
    "head of retail",
    "luxury retail buyer",
    "managing director",
    "procurement head",
    "purchase head",
    "regional sales manager",
    "retail head",
    "store operations head",
)

INDIA_FRONTLINE_ROLE_TERMS = (
    "area sales manager",
    "assistant store manager",
    "brand manager",
    "business development manager",
    "client advisor",
    "counter manager",
    "dealer development manager",
    "relationship manager",
    "retail sales executive",
    "sales consultant",
    "store manager",
)

INDIA_HIRING_ROLE_TERMS = INDIA_DECISION_ROLE_TERMS + INDIA_FRONTLINE_ROLE_TERMS

INDIA_HIRING_PLATFORMS = (
    HiringPlatform(
        key="naukri",
        label="Naukri",
        site_query="naukri.com/job-listings",
        host_suffixes=("naukri.com",),
        priority=0,
    ),
    HiringPlatform(
        key="indeed_india",
        label="Indeed India",
        site_query="in.indeed.com",
        host_suffixes=("in.indeed.com",),
        priority=1,
    ),
    HiringPlatform(
        key="foundit",
        label="Foundit",
        site_query="foundit.in/job",
        host_suffixes=("foundit.in",),
        priority=1,
    ),
)


class IndiaHiringSignalService:
    """Collect public India hiring evidence without accessing gated resume databases."""

    def __init__(self, config: Any, public_search: PublicSearchClient | None = None):
        self.config = config
        self.cfg = (
            getattr(config, "raw", {})
            .get("sourcing", {})
            .get("india_hiring_signals", {})
        )
        self.public_search = public_search

    def enrich_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(seed)
        if enriched.get("india_hiring_signal_checked"):
            return enriched
        enriched["india_hiring_signal_checked"] = True
        company_name = str(enriched.get("company_name") or "").strip()
        if (
            not is_india_account(enriched)
            or not company_name
            or self.cfg.get("enabled", True) is False
            or not self.public_search
        ):
            return enriched

        max_queries = max(1, min(3, int(self.cfg.get("max_queries_per_company") or 3)))
        result_limit = max(1, min(10, int(self.cfg.get("max_results_per_query") or 5)))
        stop_after_first_match = bool(self.cfg.get("stop_after_first_match", True))
        signals: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for platform in INDIA_HIRING_PLATFORMS[:max_queries]:
            query = build_platform_hiring_query(company_name, platform)
            try:
                rows = self.public_search.search(
                    query,
                    limit=result_limit,
                    country="IN",
                    search_lang="en",
                    extra_snippets=True,
                )
            except Exception as exc:
                log(
                    "india_hiring_signals.search_failed",
                    company=company_name,
                    platform=platform.key,
                    error=str(exc)[:500],
                )
                continue

            for signal in parse_platform_hiring_signals(
                rows,
                company_name,
                platform=platform,
                country="IN",
                location=enriched.get("location"),
                role_terms=INDIA_HIRING_ROLE_TERMS,
                decision_role_terms=INDIA_DECISION_ROLE_TERMS,
            ):
                source_url = str(signal.get("source_url") or "")
                if not source_url or source_url in seen_urls:
                    continue
                seen_urls.add(source_url)
                signals.append(signal)
            if signals and stop_after_first_match:
                break

        if not signals:
            return enriched

        score = score_hiring_signals(
            signals,
            location=enriched.get("location"),
            industry=enriched.get("industry") or enriched.get("category"),
        )
        summary = summarize_hiring_signals(
            company_name,
            signals,
            score,
            country="India",
            vertical=detect_sales_vertical(enriched),
        )
        enriched["hiring_signals"] = _merge_signals(enriched.get("hiring_signals"), signals)
        enriched["hiring_signal_summary"] = summary
        enriched["expansion_score"] = max(_safe_int(enriched.get("expansion_score")), score)
        enriched["signal_source"] = "india_public_jobs"
        existing_reason = str(enriched.get("reason") or "").strip()
        if summary and summary.casefold() not in existing_reason.casefold():
            enriched["reason"] = " ".join(part for part in (existing_reason, summary) if part)
        return enriched

    def enrich_criteria(self, criteria: dict[str, Any]) -> dict[str, Any]:
        if criteria.get("india_hiring_signal_checked"):
            return dict(criteria)
        seed = {
            "company_name": criteria.get("company_keyword") or criteria.get("company_name"),
            "company_domain": criteria.get("company_website"),
            "website": criteria.get("company_website"),
            "location": criteria.get("location"),
            "country": criteria.get("country"),
            "industry": criteria.get("industry"),
            "category": criteria.get("seed_category"),
            "reason": criteria.get("seed_reason"),
            "hiring_signals": criteria.get("hiring_signals") or [],
            "expansion_score": criteria.get("expansion_score") or 0,
        }
        enriched_seed = self.enrich_seed(seed)
        enriched = dict(criteria)
        enriched["india_hiring_signal_checked"] = True
        for source_key, target_key in (
            ("reason", "seed_reason"),
            ("hiring_signals", "hiring_signals"),
            ("hiring_signal_summary", "hiring_signal_summary"),
            ("expansion_score", "expansion_score"),
            ("signal_source", "signal_source"),
        ):
            value = enriched_seed.get(source_key)
            if value not in (None, "", []):
                enriched[target_key] = value
        return enriched


def is_india_account(seed: dict[str, Any]) -> bool:
    profile = detect_regional_profile(
        seed.get("location"),
        seed.get("country"),
        seed.get("industry"),
        seed.get("category"),
    )
    return profile.country == "IN"


def platforms_for_india_account(seed: dict[str, Any]) -> list[HiringPlatform]:
    return list(INDIA_HIRING_PLATFORMS) if is_india_account(seed) else []


def _merge_signals(existing: Any, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*(existing if isinstance(existing, list) else []), *new]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("source_url") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:10]


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "INDIA_DECISION_ROLE_TERMS",
    "INDIA_HIRING_PLATFORMS",
    "INDIA_HIRING_ROLE_TERMS",
    "IndiaHiringSignalService",
    "is_india_account",
    "platforms_for_india_account",
]
