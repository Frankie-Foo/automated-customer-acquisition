from __future__ import annotations

import urllib.parse
from typing import Any

from .clients import _domain_from_website
from .logging_utils import log
from .regional_sourcing import detect_regional_profile
from .southeast_asia_hiring_signals import (
    HiringPlatform,
    PublicSearchClient,
    detect_sales_vertical,
    parse_platform_hiring_signals,
    score_hiring_signals,
    summarize_hiring_signals,
)


IRAN_DECISION_ROLE_TERMS = (
    "مدیرعامل",
    "مدیر ارشد",
    "مدیر بازرگانی",
    "مدیر فروش",
    "مدیر خرده فروشی",
    "مدیر فروشگاه",
    "مدیر توسعه کسب و کار",
    "مدیر بازاریابی",
    "مدیر خرید",
    "مدیر محصول",
    "مدیر طراحی",
    "chief executive officer",
    "managing director",
    "general manager",
    "commercial director",
    "sales director",
    "retail director",
    "store director",
    "business development director",
    "marketing director",
    "procurement manager",
    "product director",
    "design director",
    "head of sales",
    "head of marketing",
    "country manager",
)

IRAN_FRONTLINE_ROLE_TERMS = (
    "کارشناس فروش",
    "فروشنده",
    "مسئول فروشگاه",
    "کارشناس بازاریابی",
    "کارشناس خرید",
    "طراح صنعتی",
    "طراح محصول",
    "طراح گرافیک",
    "مدیر شبکه های اجتماعی",
    "business development manager",
    "marketing manager",
    "sales manager",
    "store manager",
    "retail manager",
    "sales consultant",
    "client advisor",
    "buyer",
    "product designer",
    "industrial designer",
    "marketing specialist",
    "content marketing specialist",
    "sales specialist",
    "sales executive",
    "retail sales",
    "graphic designer",
)

IRAN_HIRING_ROLE_TERMS = IRAN_DECISION_ROLE_TERMS + IRAN_FRONTLINE_ROLE_TERMS

IRAN_HIRING_PLATFORMS = (
    HiringPlatform(
        key="irantalent",
        label="IranTalent",
        site_query="irantalent.com",
        host_suffixes=("irantalent.com",),
        priority=0,
    ),
    HiringPlatform(
        key="jobvision",
        label="JobVision",
        site_query="jobvision.ir",
        host_suffixes=("jobvision.ir",),
        priority=0,
    ),
    HiringPlatform(
        key="jobinja",
        label="Jobinja",
        site_query="jobinja.ir",
        host_suffixes=("jobinja.ir",),
        priority=1,
    ),
    HiringPlatform(
        key="divar_jobs",
        label="Divar Jobs",
        site_query="divar.ir/s",
        host_suffixes=("divar.ir",),
        priority=1,
    ),
    HiringPlatform(
        key="sheypoor_jobs",
        label="Sheypoor Jobs",
        site_query="sheypoor.com",
        host_suffixes=("sheypoor.com",),
        priority=2,
    ),
    HiringPlatform(
        key="e_estekhdam",
        label="e-estekhdam",
        site_query="e-estekhdam.com",
        host_suffixes=("e-estekhdam.com",),
        priority=2,
    ),
)


class IranHiringSignalService:
    """Collect public Iranian hiring signals without accessing gated resume data."""

    def __init__(self, config: Any, public_search: PublicSearchClient | None = None):
        self.config = config
        self.cfg = (
            getattr(config, "raw", {})
            .get("sourcing", {})
            .get("iran_hiring_signals", {})
        )
        self.public_search = public_search

    def enrich_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(seed)
        if enriched.get("iran_hiring_signal_checked"):
            return enriched
        enriched["iran_hiring_signal_checked"] = True
        company_name = str(enriched.get("company_name") or "").strip()
        if (
            not is_iran_account(enriched)
            or not company_name
            or self.cfg.get("enabled", True) is False
            or not self.public_search
        ):
            return enriched

        max_queries = max(1, min(6, int(self.cfg.get("max_queries_per_company") or 3)))
        result_limit = max(1, min(10, int(self.cfg.get("max_results_per_query") or 5)))
        stop_after_first_match = bool(self.cfg.get("stop_after_first_match", True))
        signals: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for platform in IRAN_HIRING_PLATFORMS[:max_queries]:
            query = build_iran_hiring_query(company_name, platform)
            try:
                rows = self.public_search.search(
                    query,
                    limit=result_limit,
                    country="IR",
                    search_lang="fa",
                    extra_snippets=True,
                )
            except Exception as exc:
                log(
                    "iran_hiring_signals.search_failed",
                    company=company_name,
                    platform=platform.key,
                    error=str(exc)[:500],
                )
                continue

            for signal in parse_iran_hiring_signals(
                rows,
                company_name,
                platform=platform,
                country="IR",
                location=enriched.get("location"),
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
            country="Iran",
            vertical=detect_sales_vertical(enriched),
        )
        enriched["hiring_signals"] = _merge_signals(enriched.get("hiring_signals"), signals)
        enriched["hiring_signal_summary"] = summary
        enriched["expansion_score"] = max(_safe_int(enriched.get("expansion_score")), score)
        enriched["signal_source"] = "iran_public_jobs"
        existing_reason = str(enriched.get("reason") or "").strip()
        if summary and summary.casefold() not in existing_reason.casefold():
            enriched["reason"] = " ".join(part for part in (existing_reason, summary) if part)
        return enriched

    def enrich_criteria(self, criteria: dict[str, Any]) -> dict[str, Any]:
        if criteria.get("iran_hiring_signal_checked"):
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
        enriched["iran_hiring_signal_checked"] = True
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


def is_iran_account(seed: dict[str, Any]) -> bool:
    profile = detect_regional_profile(
        seed.get("location"),
        seed.get("country"),
        seed.get("industry"),
        seed.get("category"),
    )
    if profile.country == "IR":
        return True
    domain = _domain_from_website(seed.get("company_domain") or seed.get("website") or "")
    return bool(domain and domain.endswith(".ir"))


def platforms_for_iran_account(seed: dict[str, Any]) -> list[HiringPlatform]:
    return list(IRAN_HIRING_PLATFORMS) if is_iran_account(seed) else []


def build_iran_hiring_query(company_name: str, platform: HiringPlatform) -> str:
    return f'site:{platform.site_query} "{company_name}" استخدام'


def parse_iran_hiring_signals(
    rows: list[dict[str, Any]],
    company_name: str,
    *,
    platform: HiringPlatform,
    country: str = "IR",
    location: Any = "",
) -> list[dict[str, Any]]:
    signals = parse_platform_hiring_signals(
        rows,
        company_name,
        platform=platform,
        country=country,
        location=location,
        role_terms=IRAN_HIRING_ROLE_TERMS,
        decision_role_terms=IRAN_DECISION_ROLE_TERMS,
    )
    filtered: list[dict[str, Any]] = []
    for signal in signals:
        title = _normalize(signal.get("job_title"))
        role_match = _normalize(signal.get("role_match"))
        path = urllib.parse.unquote(
            urllib.parse.urlparse(str(signal.get("source_url") or "")).path
        ).casefold()
        company_listing = (
            "/companies/" in path
            or "/company/" in path
            or "job-positions" in path
        )
        if role_match == "multiple_openings" or role_match in title or company_listing:
            filtered.append(signal)
    return filtered


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


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


__all__ = [
    "IRAN_DECISION_ROLE_TERMS",
    "IRAN_HIRING_PLATFORMS",
    "IRAN_HIRING_ROLE_TERMS",
    "IranHiringSignalService",
    "build_iran_hiring_query",
    "is_iran_account",
    "parse_iran_hiring_signals",
    "platforms_for_iran_account",
]
