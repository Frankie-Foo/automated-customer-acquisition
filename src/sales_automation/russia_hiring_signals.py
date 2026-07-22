from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Protocol

from .clients import _domain_from_website
from .http import HttpClient
from .logging_utils import log
from .regional_rules import is_low_quality_domain
from .regional_sourcing import detect_regional_profile


HH_API_BASE = "https://api.hh.ru"

RUSSIA_HIRING_ROLE_TERMS = (
    "директор магазина",
    "руководитель магазина",
    "байер",
    "категорийный менеджер",
    "директор по закупкам",
    "коммерческий директор",
    "директор по развитию",
    "директор по рознице",
    "руководитель розничной сети",
    "продавец-консультант",
    "менеджер по продажам",
    "администратор магазина",
    "администратор бутика",
    "store director",
    "store manager",
    "sales consultant",
    "buyer",
    "category manager",
    "commercial director",
    "business development director",
    "retail director",
)

RUSSIA_LUXURY_TERMS = (
    "luxury",
    "premium",
    "fashion",
    "jewelry",
    "jewellery",
    "watch",
    "boutique",
    "retail",
    "люкс",
    "премиум",
    "мода",
    "ювелир",
    "часы",
    "бутик",
    "рознич",
)

RUSSIA_LOCATION_TERMS = (
    "russia",
    "russian federation",
    "россия",
    "российская федерация",
    "moscow",
    "москва",
    "saint petersburg",
    "st petersburg",
    "санкт-петербург",
)

LEGAL_FORM_NOISE = {
    "ao",
    "company",
    "group",
    "inc",
    "llc",
    "ltd",
    "oao",
    "ooo",
    "pao",
    "зао",
    "компания",
    "ооо",
    "пао",
    "ао",
}


class HiringSignalClient(Protocol):
    def search_company_vacancies(self, company_name: str, *, limit: int = 10) -> list[dict[str, Any]]:
        ...

    def get_employer(self, employer_id: str) -> dict[str, Any]:
        ...


class PublicSearchClient(Protocol):
    def search(self, query: str, *, limit: int = 10, **options: Any) -> list[dict[str, Any]]:
        ...


class HeadHunterPublicClient:
    """Small client for public company and vacancy signals from the official hh.ru API."""

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient(timeout=8, retries=1)

    def search_company_vacancies(self, company_name: str, *, limit: int = 10) -> list[dict[str, Any]]:
        params = [
            ("text", company_name),
            ("search_field", "company_name"),
            ("area", "113"),
            ("order_by", "publication_time"),
            ("per_page", str(max(1, min(20, int(limit or 10))))),
        ]
        payload = self.http.request(
            "GET",
            f"{HH_API_BASE}/vacancies?{urllib.parse.urlencode(params)}",
            headers={"User-Agent": "outbound-ops/1.0 (sales-automation)"},
            retries=1,
        )
        return list(payload.get("items") or [])

    def get_employer(self, employer_id: str) -> dict[str, Any]:
        if not str(employer_id or "").strip():
            return {}
        return self.http.request(
            "GET",
            f"{HH_API_BASE}/employers/{urllib.parse.quote(str(employer_id))}",
            headers={"User-Agent": "outbound-ops/1.0 (sales-automation)"},
            retries=1,
        )


class RussiaHiringSignalService:
    """Adds public hiring/expansion evidence to Russian account seeds without blocking sourcing."""

    def __init__(
        self,
        config: Any,
        client: HiringSignalClient | None = None,
        public_search: PublicSearchClient | None = None,
    ):
        self.config = config
        self.cfg = (
            getattr(config, "raw", {})
            .get("sourcing", {})
            .get("russia_hiring_signals", {})
        )
        self.client = client or HeadHunterPublicClient()
        self.public_search = public_search

    def enrich_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(seed)
        if enriched.get("hiring_signal_checked") or not self._enabled_for(enriched):
            return enriched
        enriched["hiring_signal_checked"] = True
        company_name = str(enriched.get("company_name") or "").strip()
        if not company_name:
            return enriched
        limit = max(1, min(20, int(self.cfg.get("max_vacancies_per_company") or 10)))
        signals: list[dict[str, Any]] = []
        employer: dict[str, Any] = {}
        if bool(self.cfg.get("official_api_enabled", False)):
            try:
                rows = self.client.search_company_vacancies(company_name, limit=limit)
                signals = parse_company_hiring_signals(rows, company_name, location=enriched.get("location"))
                employer = self._employer_details(signals)
            except Exception as exc:
                log("russia_hiring_signals.hh_api_failed", company=company_name, error=str(exc)[:500])
        if not signals and self.public_search:
            try:
                signals = self._public_search_signals(enriched, limit=limit)
            except Exception as exc:
                log("russia_hiring_signals.public_search_failed", company=company_name, error=str(exc)[:500])
        if not signals:
            return enriched

        score = score_hiring_signals(signals, location=enriched.get("location"), industry=enriched.get("industry") or enriched.get("category"))
        summary = summarize_hiring_signals(company_name, signals, score)
        enriched["hiring_signals"] = signals
        enriched["hiring_signal_summary"] = summary
        enriched["expansion_score"] = score
        enriched["signal_source"] = "hh.ru_public_vacancies"

        site_url = str(employer.get("site_url") or "").strip()
        site_domain = _domain_from_website(site_url)
        if not enriched.get("company_domain") and site_domain and not is_low_quality_domain(site_domain):
            enriched["company_domain"] = site_domain
            enriched["website"] = site_url

        existing_reason = str(enriched.get("reason") or "").strip()
        if summary and summary.casefold() not in existing_reason.casefold():
            enriched["reason"] = " ".join(part for part in (existing_reason, summary) if part)
        return enriched

    def enrich_criteria(self, criteria: dict[str, Any]) -> dict[str, Any]:
        if criteria.get("hiring_signal_checked"):
            return dict(criteria)
        seed = {
            "company_name": criteria.get("company_keyword") or criteria.get("company_name"),
            "company_domain": criteria.get("company_website"),
            "website": criteria.get("company_website"),
            "location": criteria.get("location"),
            "industry": criteria.get("industry"),
            "category": criteria.get("seed_category"),
            "reason": criteria.get("seed_reason"),
        }
        enriched_seed = self.enrich_seed(seed)
        enriched = dict(criteria)
        enriched["hiring_signal_checked"] = True
        for source_key, target_key in (
            ("company_domain", "company_website"),
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

    def _enabled_for(self, seed: dict[str, Any]) -> bool:
        if self.cfg.get("enabled", True) is False:
            return False
        return is_russia_account(seed)

    def _employer_details(self, signals: list[dict[str, Any]]) -> dict[str, Any]:
        employer_id = next((str(item.get("employer_id") or "") for item in signals if item.get("employer_id")), "")
        if not employer_id:
            return {}
        try:
            return self.client.get_employer(employer_id)
        except Exception as exc:
            log("russia_hiring_signals.employer_failed", employer_id=employer_id, error=str(exc)[:500])
            return {}

    def _public_search_signals(self, seed: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
        company_name = str(seed.get("company_name") or "").strip()
        query = build_public_hiring_query(company_name)
        rows = self.public_search.search(
            query,
            limit=min(10, limit),
            country="RU",
            search_lang="ru",
            extra_snippets=True,
        )
        return parse_public_search_hiring_signals(rows, company_name, location=seed.get("location"))


def is_russia_account(seed: dict[str, Any]) -> bool:
    profile = detect_regional_profile(
        seed.get("location"),
        seed.get("country"),
        seed.get("industry"),
        seed.get("category"),
    )
    if profile.key == "russia":
        return True
    domain = _domain_from_website(seed.get("company_domain") or seed.get("website") or "")
    if domain.endswith((".ru", ".рф")):
        return True
    text = _normalize(" ".join(str(seed.get(key) or "") for key in ("location", "country", "reason")))
    return any(term in text for term in RUSSIA_LOCATION_TERMS)


def parse_company_hiring_signals(
    rows: list[dict[str, Any]],
    company_name: str,
    *,
    location: Any = "",
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    expected = _company_tokens(company_name)
    location_text = _normalize(location)
    for row in rows:
        employer = row.get("employer") if isinstance(row.get("employer"), dict) else {}
        observed_name = str(employer.get("name") or "").strip()
        if not expected or not _company_names_match(expected, _company_tokens(observed_name)):
            continue
        title = str(row.get("name") or "").strip()
        area = row.get("area") if isinstance(row.get("area"), dict) else {}
        city = str(area.get("name") or "").strip()
        snippet = row.get("snippet") if isinstance(row.get("snippet"), dict) else {}
        evidence_text = _normalize(" ".join(str(value or "") for value in (title, snippet.get("requirement"), snippet.get("responsibility"))))
        role_match = next((term for term in RUSSIA_HIRING_ROLE_TERMS if term in evidence_text), "")
        luxury_match = next((term for term in RUSSIA_LUXURY_TERMS if term in evidence_text), "")
        signals.append({
            "type": "retail_hiring",
            "source": "hh.ru",
            "source_url": str(row.get("alternate_url") or ""),
            "vacancy_id": str(row.get("id") or ""),
            "employer_id": str(employer.get("id") or ""),
            "employer_name": observed_name,
            "job_title": title,
            "city": city,
            "published_at": str(row.get("published_at") or ""),
            "role_match": role_match,
            "luxury_match": luxury_match,
            "location_match": bool(location_text and location_text in _normalize(city)),
        })
    return signals


def build_public_hiring_query(company_name: str) -> str:
    # Keep the provider query broad and apply strict employer/role filtering locally.
    # Search engines often drop valid hh.ru pages when several quoted OR clauses are combined.
    return f'site:hh.ru/vacancy "{company_name}"'


def parse_public_search_hiring_signals(
    rows: list[dict[str, Any]],
    company_name: str,
    *,
    location: Any = "",
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    expected = _company_tokens(company_name)
    location_text = _normalize(location)
    for row in rows:
        url = str(row.get("link") or row.get("url") or "").strip()
        parsed_url = urllib.parse.urlparse(url)
        if parsed_url.hostname not in {"hh.ru", "www.hh.ru"} or not parsed_url.path.startswith("/vacancy/"):
            continue
        title = re.sub(r"\s*[|—-]\s*hh\.ru.*$", "", str(row.get("title") or "").strip(), flags=re.I)
        snippet = str(row.get("snippet") or row.get("description") or "").strip()
        evidence_text = _normalize(f"{title} {snippet}")
        if any(marker in evidence_text for marker in ("вакансия в архиве", "архивная вакансия", "archived vacancy", "vacancy archived")):
            continue
        if not _company_names_match(expected, _company_tokens(evidence_text)):
            continue
        role_match = next((term for term in RUSSIA_HIRING_ROLE_TERMS if term in evidence_text), "")
        if not role_match:
            continue
        luxury_match = next((term for term in RUSSIA_LUXURY_TERMS if term in evidence_text), "")
        city = _detect_russian_city(evidence_text, location_text)
        signals.append({
            "type": "retail_hiring",
            "source": "hh.ru_public_search",
            "source_url": url,
            "vacancy_id": parsed_url.path.rstrip("/").split("/")[-1],
            "employer_id": "",
            "employer_name": company_name,
            "job_title": title,
            "city": city,
            "published_at": str(row.get("published_at") or ""),
            "role_match": role_match,
            "luxury_match": luxury_match,
            "location_match": bool(location_text and (location_text in evidence_text or _normalize(city) in location_text)),
        })
    return signals


def score_hiring_signals(signals: list[dict[str, Any]], *, location: Any = "", industry: Any = "") -> int:
    if not signals:
        return 0
    score = 25
    if any(item.get("role_match") for item in signals):
        score += 25
    if any(item.get("luxury_match") for item in signals) or any(term in _normalize(industry) for term in RUSSIA_LUXURY_TERMS):
        score += 20
    if len(signals) >= 2:
        score += 15
    if len({str(item.get("city") or "").casefold() for item in signals if item.get("city")}) >= 2:
        score += 10
    if any(item.get("location_match") for item in signals):
        score += 5
    if any(_age_days(item.get("published_at")) <= 45 for item in signals if item.get("published_at")):
        score += 10
    return min(100, score)


def summarize_hiring_signals(company_name: str, signals: list[dict[str, Any]], score: int) -> str:
    if not signals:
        return ""
    titles = list(dict.fromkeys(str(item.get("job_title") or "").strip() for item in signals if item.get("job_title")))[:3]
    cities = list(dict.fromkeys(str(item.get("city") or "").strip() for item in signals if item.get("city")))[:3]
    latest = max((str(item.get("published_at") or "") for item in signals), default="")[:10]
    detail = ", ".join(titles) or "retail/commercial roles"
    city_text = ", ".join(cities) or "Russia"
    date_text = f", latest {latest}" if latest else ""
    return (
        f"Public hh.ru hiring activity suggests {company_name} is actively building its team in {city_text}: "
        f"{detail} ({len(signals)} current signals{date_text}; expansion confidence {score}/100)."
    )


def _company_names_match(expected: set[str], observed: set[str]) -> bool:
    if not expected or not observed:
        return False
    overlap = expected & observed
    return expected.issubset(observed) or observed.issubset(expected) or len(overlap) / max(len(expected), len(observed)) >= 0.6


def _detect_russian_city(text: str, fallback: str) -> str:
    cities = {
        "москва": "Москва",
        "moscow": "Москва",
        "санкт-петербург": "Санкт-Петербург",
        "saint petersburg": "Санкт-Петербург",
        "st petersburg": "Санкт-Петербург",
        "екатеринбург": "Екатеринбург",
        "казань": "Казань",
        "сочи": "Сочи",
        "новосибирск": "Новосибирск",
    }
    for needle, city in cities.items():
        if needle in text:
            return city
    return str(fallback or "Russia").split(",", 1)[0].strip()


def _company_tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9]+", _normalize(value), flags=re.I)
        if len(token) > 1 and token not in LEGAL_FORM_NOISE
    }


def _age_days(value: Any) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days)
    except (TypeError, ValueError):
        return 9999


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


__all__ = [
    "HeadHunterPublicClient",
    "RussiaHiringSignalService",
    "is_russia_account",
    "build_public_hiring_query",
    "parse_company_hiring_signals",
    "parse_public_search_hiring_signals",
    "score_hiring_signals",
    "summarize_hiring_signals",
]
