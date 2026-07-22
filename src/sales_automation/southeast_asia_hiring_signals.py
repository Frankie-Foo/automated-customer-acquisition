from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .logging_utils import log
from .regional_sourcing import detect_regional_profile


SEA_COUNTRIES = {"SG", "MY", "TH", "ID", "VN", "PH"}

DECISION_ROLE_TERMS = (
    "business development director",
    "category manager",
    "commercial director",
    "country manager",
    "general manager",
    "head of retail",
    "managing director",
    "procurement manager",
    "regional manager",
    "retail director",
    "retail manager",
    "sales director",
    "store manager",
    "buyer",
    "giám đốc",
    "quản lý cửa hàng",
    "trưởng phòng kinh doanh",
    "manajer penjualan",
    "manajer toko",
    "pengurus jualan",
    "pengurus kedai",
    "ผู้จัดการฝ่ายขาย",
    "ผู้จัดการร้าน",
)

FRONTLINE_EXPANSION_TERMS = (
    "automotive sales",
    "beauty advisor",
    "boutique manager",
    "client advisor",
    "dealer relationship",
    "product consultant",
    "promoter",
    "retail sales",
    "sales advisor",
    "sales associate",
    "sales consultant",
    "sales executive",
    "b2c sales",
    "sale xe ô tô",
    "nhân viên bán hàng",
    "tư vấn bán hàng",
    "promotor",
    "konsultan penjualan",
    "kepala toko",
    "jurujual",
    "perunding jualan",
    "พนักงานขาย",
    "ที่ปรึกษาการขาย",
)

HIRING_ROLE_TERMS = DECISION_ROLE_TERMS + FRONTLINE_EXPANSION_TERMS

VERTICAL_TERMS = {
    "luxury": (
        "luxury", "premium", "boutique", "client advisor", "beauty advisor", "jewellery",
        "jewelry", "watch", "fashion", "high-end", "mewah", "cao cấp", "หรู",
    ),
    "electronics": (
        "consumer electronics", "electronics", "smartphone", "mobile phone", "telecom",
        "gadget", "digital retail", "3c", "điện tử", "elektronik",
    ),
    "automotive": (
        "automotive", "automobile", "car dealer", "dealership", "vehicle", "sales mobil",
        "sale xe ô tô", "ô tô", "otomotif", "รถยนต์",
    ),
}

ARCHIVE_MARKERS = (
    "archived vacancy",
    "applications closed",
    "(closed)",
    "expired job",
    "job expired",
    "job is no longer available",
    "no longer accepting applications",
    "position has been filled",
    "lowongan ditutup",
    "sudah ditutup",
    "telah berakhir",
    "hết hạn nộp hồ sơ",
    "đã hết hạn",
    "ngừng tuyển",
    "ปิดรับสมัคร",
    "หมดเขตรับสมัคร",
    "jawatan telah ditutup",
)

LEGAL_FORM_NOISE = {
    "berhad", "company", "corporation", "inc", "joint", "limited", "llc", "ltd", "plc",
    "pte", "pt", "sdn", "tbk", "the", "and", "co", "corp", "company", "บริษัท",
    "công", "ty", "cổ", "phần",
}


@dataclass(frozen=True)
class HiringPlatform:
    key: str
    label: str
    site_query: str
    host_suffixes: tuple[str, ...]
    priority: int


PLATFORMS = {
    "jobstreet_sg": HiringPlatform("jobstreet", "JobStreet", "sg.jobstreet.com", ("sg.jobstreet.com",), 0),
    "mycareersfuture": HiringPlatform("mycareersfuture", "MyCareersFuture", "mycareersfuture.gov.sg", ("mycareersfuture.gov.sg",), 0),
    "glints": HiringPlatform("glints", "Glints", "glints.com", ("glints.com",), 1),
    "jobstreet_my": HiringPlatform("jobstreet", "JobStreet", "my.jobstreet.com", ("my.jobstreet.com",), 0),
    "myfuturejobs": HiringPlatform("myfuturejobs", "MyFutureJobs", "myfuturejobs.gov.my", ("myfuturejobs.gov.my",), 1),
    "maukerja": HiringPlatform("maukerja", "Maukerja", "maukerja.my", ("maukerja.my",), 1),
    "jobsdb_th": HiringPlatform("jobsdb", "JobsDB", "th.jobsdb.com", ("th.jobsdb.com",), 0),
    "jobthai": HiringPlatform("jobthai", "JobThai", "jobthai.com", ("jobthai.com",), 0),
    "jobbkk": HiringPlatform("jobbkk", "JobBKK", "jobbkk.com", ("jobbkk.com",), 1),
    "jobstreet_id": HiringPlatform("jobstreet", "JobStreet", "id.jobstreet.com", ("id.jobstreet.com",), 0),
    "dealls": HiringPlatform("dealls", "Dealls", "dealls.com", ("dealls.com",), 1),
    "kalibrr": HiringPlatform("kalibrr", "Kalibrr", "kalibrr.com", ("kalibrr.com",), 1),
    "topcv": HiringPlatform("topcv", "TopCV", "topcv.vn", ("topcv.vn",), 0),
    "vietnamworks": HiringPlatform("vietnamworks", "VietnamWorks", "vietnamworks.com", ("vietnamworks.com",), 0),
    "careerviet": HiringPlatform("careerviet", "CareerViet", "careerviet.vn", ("careerviet.vn",), 1),
    "jobstreet_ph": HiringPlatform("jobstreet", "JobStreet", "ph.jobstreet.com", ("ph.jobstreet.com",), 0),
    "onlinejobs_ph": HiringPlatform("onlinejobs", "OnlineJobs.ph", "onlinejobs.ph", ("onlinejobs.ph",), 2),
    "luxury_careers": HiringPlatform("luxury_careers", "Luxury Careers", "luxury-careers.com", ("luxury-careers.com",), 1),
}

COUNTRY_PLATFORM_ROUTES = {
    "SG": ("jobstreet_sg", "mycareersfuture", "glints"),
    "MY": ("jobstreet_my", "myfuturejobs", "maukerja"),
    "TH": ("jobsdb_th", "jobthai", "jobbkk"),
    "ID": ("jobstreet_id", "glints", "dealls", "kalibrr"),
    "VN": ("topcv", "vietnamworks", "careerviet", "glints"),
    "PH": ("jobstreet_ph", "kalibrr", "glints", "onlinejobs_ph"),
}

LOCAL_SEARCH_LANG = {"SG": "en", "MY": "ms", "TH": "th", "ID": "id", "VN": "vi", "PH": "en"}


class PublicSearchClient(Protocol):
    def search(self, query: str, *, limit: int = 10, **options: Any) -> list[dict[str, Any]]:
        ...


class SoutheastAsiaHiringSignalService:
    """Collects company-level public hiring evidence for six Southeast Asian markets."""

    def __init__(self, config: Any, public_search: PublicSearchClient | None = None):
        self.config = config
        self.cfg = (
            getattr(config, "raw", {})
            .get("sourcing", {})
            .get("southeast_asia_hiring_signals", {})
        )
        self.public_search = public_search

    def enrich_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(seed)
        if enriched.get("southeast_asia_hiring_signal_checked"):
            return enriched
        enriched["southeast_asia_hiring_signal_checked"] = True
        country = southeast_asia_country(enriched)
        company_name = str(enriched.get("company_name") or "").strip()
        if not country or not company_name or self.cfg.get("enabled", True) is False or not self.public_search:
            return enriched

        vertical = detect_sales_vertical(enriched)
        max_queries = max(1, min(4, int(self.cfg.get("max_queries_per_company") or 3)))
        result_limit = max(1, min(10, int(self.cfg.get("max_results_per_query") or 5)))
        stop_after_first_match = bool(self.cfg.get("stop_after_first_match", True))
        platforms = platforms_for_account(enriched)[:max_queries]
        signals: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for platform in platforms:
            query = build_platform_hiring_query(company_name, platform)
            try:
                rows = self.public_search.search(
                    query,
                    limit=result_limit,
                    country=country,
                    search_lang=LOCAL_SEARCH_LANG[country],
                    extra_snippets=True,
                )
            except Exception as exc:
                log(
                    "southeast_asia_hiring_signals.search_failed",
                    company=company_name,
                    country=country,
                    platform=platform.key,
                    error=str(exc)[:500],
                )
                continue
            for signal in parse_platform_hiring_signals(
                rows,
                company_name,
                platform=platform,
                country=country,
                location=enriched.get("location"),
            ):
                if signal["source_url"] in seen_urls:
                    continue
                seen_urls.add(signal["source_url"])
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
        summary = summarize_hiring_signals(company_name, signals, score, country=country, vertical=vertical)
        enriched["hiring_signals"] = _merge_signals(enriched.get("hiring_signals"), signals)
        enriched["hiring_signal_summary"] = summary
        enriched["expansion_score"] = max(_safe_int(enriched.get("expansion_score")), score)
        enriched["signal_source"] = "southeast_asia_public_jobs"
        existing_reason = str(enriched.get("reason") or "").strip()
        if summary and summary.casefold() not in existing_reason.casefold():
            enriched["reason"] = " ".join(part for part in (existing_reason, summary) if part)
        return enriched

    def enrich_criteria(self, criteria: dict[str, Any]) -> dict[str, Any]:
        if criteria.get("southeast_asia_hiring_signal_checked"):
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
        enriched["southeast_asia_hiring_signal_checked"] = True
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


def southeast_asia_country(seed: dict[str, Any]) -> str | None:
    profile = detect_regional_profile(
        seed.get("location"),
        seed.get("country"),
        seed.get("industry"),
        seed.get("category"),
    )
    if profile.key == "southeast_asia" and profile.country in SEA_COUNTRIES:
        return profile.country
    return None


def detect_sales_vertical(seed: dict[str, Any]) -> str:
    text = _normalize(" ".join(str(seed.get(key) or "") for key in ("industry", "category", "reason")))
    for vertical, terms in VERTICAL_TERMS.items():
        if any(term in text for term in terms):
            return vertical
    return "retail"


def platforms_for_account(seed: dict[str, Any]) -> list[HiringPlatform]:
    country = southeast_asia_country(seed)
    if not country:
        return []
    route = [PLATFORMS[key] for key in COUNTRY_PLATFORM_ROUTES[country]]
    if detect_sales_vertical(seed) == "luxury" and country in {"SG", "MY", "TH"}:
        route.insert(2, PLATFORMS["luxury_careers"])
    return list(dict.fromkeys(route))


def build_platform_hiring_query(company_name: str, platform: HiringPlatform) -> str:
    return f'site:{platform.site_query} "{company_name}"'


def parse_platform_hiring_signals(
    rows: list[dict[str, Any]],
    company_name: str,
    *,
    platform: HiringPlatform,
    country: str,
    location: Any = "",
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    expected = _company_tokens(company_name)
    location_text = _normalize(location)
    for row in rows:
        url = str(row.get("link") or row.get("url") or "").strip()
        parsed_url = urllib.parse.urlparse(url)
        host = str(parsed_url.hostname or "").casefold()
        if not any(host == suffix or host.endswith(f".{suffix}") for suffix in platform.host_suffixes):
            continue
        if not _looks_like_job_page(parsed_url.path):
            continue
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or row.get("description") or "").strip()
        evidence_text = _normalize(f"{title} {snippet}")
        if any(marker in evidence_text for marker in ARCHIVE_MARKERS):
            continue
        if not _company_names_match(expected, _company_tokens(evidence_text)):
            continue
        role_match = next((term for term in HIRING_ROLE_TERMS if term in evidence_text), "")
        openings_count = _extract_openings_count(evidence_text)
        if not role_match and openings_count >= 2:
            role_match = "multiple_openings"
        if not role_match:
            continue
        vertical_match = next(
            (term for terms in VERTICAL_TERMS.values() for term in terms if term in evidence_text),
            "",
        )
        signals.append({
            "type": "retail_hiring",
            "source": platform.key,
            "source_label": platform.label,
            "source_url": url,
            "country": country,
            "job_title": _clean_job_title(title, company_name, platform.label),
            "location": str(location or "").strip(),
            "published_at": str(row.get("published_at") or row.get("date") or ""),
            "role_match": role_match,
            "decision_role": role_match in DECISION_ROLE_TERMS,
            "vertical_match": vertical_match,
            "location_match": bool(location_text and location_text in evidence_text),
            "platform_priority": platform.priority,
            "openings_count": openings_count,
        })
    return signals


def score_hiring_signals(signals: list[dict[str, Any]], *, location: Any = "", industry: Any = "") -> int:
    if not signals:
        return 0
    score = 20
    if any(item.get("decision_role") for item in signals):
        score += 25
    elif any(item.get("role_match") for item in signals):
        score += 15
    industry_text = _normalize(industry)
    if any(item.get("vertical_match") for item in signals) or any(
        term in industry_text for terms in VERTICAL_TERMS.values() for term in terms
    ):
        score += 20
    if len(signals) >= 2:
        score += 15
    elif max((_safe_int(item.get("openings_count")) for item in signals), default=0) >= 5:
        score += 15
    if len({item.get("source") for item in signals}) >= 2:
        score += 10
    if any(item.get("location_match") for item in signals):
        score += 5
    if any(_age_days(item.get("published_at")) <= 45 for item in signals if item.get("published_at")):
        score += 10
    return min(100, score)


def summarize_hiring_signals(
    company_name: str,
    signals: list[dict[str, Any]],
    score: int,
    *,
    country: str,
    vertical: str,
) -> str:
    if not signals:
        return ""
    platforms = list(dict.fromkeys(str(item.get("source_label") or item.get("source") or "") for item in signals))[:3]
    titles = list(dict.fromkeys(str(item.get("job_title") or "").strip() for item in signals if item.get("job_title")))[:3]
    openings = max((_safe_int(item.get("openings_count")) for item in signals), default=0)
    platform_text = ", ".join(platforms)
    title_text = ", ".join(titles) or "sales and retail roles"
    volume_text = f"; at least {openings} openings reported" if openings else ""
    return (
        f"Public {platform_text} listings indicate {company_name} may be building its {vertical} sales or retail team "
        f"in {country}: {title_text} ({len(signals)} current signals{volume_text}; expansion confidence {score}/100)."
    )


def _looks_like_job_page(path: str) -> bool:
    normalized = str(path or "").casefold()
    markers = (
        "/job", "/jobs", "-jobs", "/opportunities/", "/explore/", "/lowongan",
        "/viec-lam", "/tuyen-dung", "/career",
    )
    return any(marker in normalized for marker in markers)


def _extract_openings_count(text: str) -> int:
    patterns = (
        r"\b(\d{1,4})\b[^0-9.]{0,50}\b(?:jobs?|positions?|vacancies|openings|pekerjaan|lowongan|việc làm)\b",
        r"\b(?:jobs?|positions?|vacancies|openings|pekerjaan|lowongan|việc làm)\b[^0-9.]{0,30}\b(\d{1,4})\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            value = int(match.group(1))
            if 1900 <= value <= 2100:
                continue
            return min(1000, value)
    return 0


def _clean_job_title(title: str, company_name: str, platform_label: str) -> str:
    cleaned = re.sub(rf"\s*[|—-]\s*{re.escape(platform_label)}.*$", "", title, flags=re.I)
    cleaned = re.sub(rf"\s+(?:at|@|-|–|—)\s+{re.escape(company_name)}.*$", "", cleaned, flags=re.I)
    return cleaned.strip()[:240]


def _company_names_match(expected: set[str], observed: set[str]) -> bool:
    if not expected or not observed:
        return False
    overlap = expected & observed
    return expected.issubset(observed) or len(overlap) / len(expected) >= 0.75


def _company_tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9\u00c0-\u024f\u0e00-\u0e7f\u1e00-\u1eff]+", _normalize(value), flags=re.I)
        if len(token) > 1 and token not in LEGAL_FORM_NOISE
    }


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


def _age_days(value: Any) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days)
    except (TypeError, ValueError):
        return 9999


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


__all__ = [
    "HiringPlatform",
    "SoutheastAsiaHiringSignalService",
    "build_platform_hiring_query",
    "detect_sales_vertical",
    "parse_platform_hiring_signals",
    "platforms_for_account",
    "score_hiring_signals",
    "southeast_asia_country",
    "summarize_hiring_signals",
]
