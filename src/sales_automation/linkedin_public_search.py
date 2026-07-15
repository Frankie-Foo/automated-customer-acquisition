from __future__ import annotations

import re
import socket
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from ipaddress import ip_address
from typing import Any, Protocol

from .clients import HunterClient, ProspeoClient, _domain_from_website, is_full_email
from .config import AppConfig
from .db import Repository
from .email_discovery import EmailCandidate, guess_email_candidates
from .http import HttpClient
from .logging_utils import log
from .regional_rules import is_low_quality_domain, mapped_middle_east_domain
from .regional_sourcing import detect_regional_profile, regional_role_terms, search_options

BLOCKED_DOMAINS = {
    "linkedin.com",
    "www.linkedin.com",
    "wikipedia.org",
    "www.wikipedia.org",
    "crunchbase.com",
    "www.crunchbase.com",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "twitter.com",
    "x.com",
}

BRAVE_SUPPORTED_COUNTRIES = {
    "AR", "AU", "AT", "BE", "BR", "CA", "CL", "DK", "FI", "FR", "DE", "GR", "HK", "IN", "ID",
    "IT", "JP", "KR", "MY", "MX", "NL", "NZ", "NO", "CN", "PL", "PT", "PH", "RU", "SA", "ZA", "ES",
    "SE", "CH", "TW", "TR", "GB", "US", "ALL",
}

PUBLIC_MAILBOX_DOMAINS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "mail.ru", "yandex.ru", "proton.me"}


class GoogleCSEClient:
    def __init__(self, api_key: str, cse_id: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.cse_id = cse_id
        self.http = http or HttpClient(timeout=30)

    def search(self, query: str, *, limit: int = 10, **options: Any) -> list[dict[str, Any]]:
        params = {
            "key": self.api_key,
            "cx": self.cse_id,
            "q": query,
            "num": max(1, min(10, int(limit or 10))),
        }
        url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(params)
        data = self.http.request("GET", url)
        return data.get("items") or []


class SearchClient(Protocol):
    def search(self, query: str, *, limit: int = 10, **options: Any) -> list[dict[str, Any]]:
        ...


class TavilySearchClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient(timeout=30)

    def search(self, query: str, *, limit: int = 10, **options: Any) -> list[dict[str, Any]]:
        data = self.http.request(
            "POST",
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json_body={
                "query": query,
                "search_depth": "basic",
                "max_results": max(1, min(10, int(limit or 10))),
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        results = data.get("results") or []
        return [
            {
                "title": item.get("title") or item.get("url") or "",
                "snippet": item.get("content") or item.get("snippet") or "",
                "link": item.get("url") or item.get("link") or "",
                "published_at": item.get("published_date") or item.get("published_at") or "",
            }
            for item in results
        ]


class BraveSearchClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient(timeout=30)

    def search(self, query: str, *, limit: int = 10, **options: Any) -> list[dict[str, Any]]:
        params = {
            "q": query,
            "count": max(1, min(20, int(limit or 10))),
            "search_lang": options.get("search_lang") or "en",
            "safesearch": "moderate",
            "text_decorations": "false",
            "extra_snippets": "true" if options.get("extra_snippets") else "false",
        }
        if options.get("country"):
            requested_country = str(options["country"]).upper()
            params["country"] = requested_country if requested_country in BRAVE_SUPPORTED_COUNTRIES else "ALL"
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
        data = self.http.request(
            "GET",
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
        )
        results = (data.get("web") or {}).get("results") or []
        return [
            {
                "title": item.get("title") or item.get("url") or "",
                "snippet": " ".join(filter(None, [item.get("description") or item.get("snippet") or "", *(item.get("extra_snippets") or [])])),
                "link": item.get("url") or "",
                "published_at": item.get("page_age") or item.get("age") or item.get("published_at") or "",
            }
            for item in results
        ]


class FallbackSearchClient:
    def __init__(self, clients: list[tuple[str, SearchClient]]):
        self.clients = clients
        self.last_provider = clients[0][0] if clients else ""

    def search(self, query: str, *, limit: int = 10, **options: Any) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for name, client in self.clients:
            try:
                results = client.search(query, limit=limit, **options)
                self.last_provider = name
                return results
            except Exception as exc:
                last_error = exc
                log("linkedin_public_search.provider_failed", provider=name, error=str(exc)[:500])
                continue
        if last_error:
            raise last_error
        return []


class CompanyDomainResolver:
    def __init__(self, client: SearchClient | None = None):
        self.client = client

    def resolve(self, company_name: str | None, existing_domain: str | None = None, *, location: str | None = None, category: str | None = None) -> str | None:
        mapped = mapped_middle_east_domain(company_name, location=location, category=category)
        if mapped:
            return mapped
        domain = _domain_from_website(existing_domain or "")
        if domain and not _is_blocked_domain(domain) and not is_low_quality_domain(domain):
            return domain
        if not company_name or not self.client:
            return None
        criteria = {"location": location or "", "seed_category": category or ""}
        profile = detect_regional_profile(location, category)
        for index, phrase in enumerate(profile.channel_terms[:2]):
            options = search_options(criteria)[index % len(search_options(criteria))]
            query = f'"{company_name}" "{phrase}"'
            try:
                results = self.client.search(query, limit=5, **options)
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                results = self.client.search(query, limit=5)
            for item in results:
                candidate = _domain_from_website(item.get("link") or "")
                if candidate and not _is_blocked_domain(candidate) and not is_low_quality_domain(candidate):
                    return candidate
        return None


class LinkedInPublicSearchService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo
        self.cfg = config.raw.get("sourcing", {}).get("linkedin_public_search", {})
        self.client = build_search_client(config)
        self.domain_resolver = CompanyDomainResolver(self.client)

    def run(self, criteria: dict[str, Any], limit: int, *, user: dict[str, Any]) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Missing search API config: set Google CSE or TAVILY_API_KEY")
        max_queries = int(self.cfg.get("max_queries_per_run") or 10)
        has_target_name = bool(_target_name(criteria))
        min_score = int(criteria.get("min_lead_score") or (70 if has_target_name else self.cfg.get("min_lead_score") or 50))
        task = self.repo.create_lead_search_task(
            criteria=criteria,
            provider="linkedin_public_search",
            requested_limit=limit,
            created_by_user_id=int(user["id"]),
            owner_user_id=int(user["id"]),
        )
        all_results: list[dict[str, Any]] = []
        promoted = skipped = 0
        queries = build_linkedin_queries(criteria)[:max_queries]
        auto_domain_lookup = bool(criteria.get("auto_domain_lookup", self.cfg.get("auto_domain_lookup", True)))
        auto_generate_candidates = bool(criteria.get("auto_generate_email_candidates", self.cfg.get("auto_generate_email_candidates", True)))
        try:
            seen_urls: set[str] = set()
            query_options = search_options(criteria)
            for query_index, query in enumerate(queries):
                remaining = max(0, limit - len(all_results))
                if remaining <= 0:
                    break
                options = query_options[query_index % len(query_options)]
                for item in self.client.search(query, limit=min(10, remaining), **options):
                    parsed = parse_linkedin_search_item(item, criteria)
                    if not parsed or parsed["linkedin_url"] in seen_urls:
                        continue
                    seen_urls.add(parsed["linkedin_url"])
                    if auto_domain_lookup:
                        parsed["company_domain"] = self.domain_resolver.resolve(
                            parsed.get("company_name"),
                            parsed.get("company_domain") or criteria.get("company_website"),
                            location=parsed.get("location") or criteria.get("location"),
                            category=parsed.get("industry") or criteria.get("industry") or criteria.get("seed_category"),
                        )
                    parsed["lead_score"], parsed["match_evidence"] = score_lead_details(parsed, criteria)
                    parsed["match_confidence"] = parsed["lead_score"]
                    parsed["match_status"] = classify_identity_match(parsed, criteria)
                    if auto_generate_candidates:
                        generated = [asdict(item) for item in generate_public_search_email_candidates(parsed, self.repo)]
                        parsed["email_candidates"] = _merge_email_candidates(generated, criteria.get("company_email_candidates") or [])
                    status = "candidate" if parsed["lead_score"] >= min_score and parsed["match_status"] != "mismatch" else "low_score"
                    result = self.repo.create_lead_search_result(task["id"], parsed, status=status)
                    all_results.append(result)
                    if parsed["lead_score"] < min_score or parsed["match_status"] == "mismatch":
                        skipped += 1
                        continue
                    contact = _contact_from_search_result(parsed, task["id"])
                    duplicate = self.repo.find_duplicate_contact(contact)
                    if duplicate:
                        skipped += 1
                        self.repo.mark_lead_search_result_promoted(result["id"], duplicate["id"])
                        self.repo.update_lead_search_result_status(result["id"], "duplicate", "duplicate_contact")
                        continue
                    inserted, _ = self.repo.upsert_contacts(
                        [contact],
                        pool_type="public",
                    )
                    if inserted:
                        promoted += 1
                        promoted_contact = self.repo.get_contact_by_linkedin_url(parsed["linkedin_url"])
                        if promoted_contact:
                            self.repo.mark_lead_search_result_promoted(result["id"], promoted_contact["id"])
                            if bool(criteria.get("high_confidence_verify", True)):
                                self._verify_high_confidence_candidates(promoted_contact, parsed.get("email_candidates") or [])
                    else:
                        skipped += 1
                        self.repo.update_lead_search_result_status(result["id"], "duplicate", "duplicate_or_existing_contact")
            self.repo.complete_lead_search_task(task["id"], query_count=len(queries), result_count=len(all_results), promoted_count=promoted, skipped_count=skipped)
        except Exception as exc:
            self.repo.complete_lead_search_task(task["id"], query_count=len(queries), result_count=len(all_results), promoted_count=promoted, skipped_count=skipped, error=str(exc))
            raise
        log("linkedin_public_search.completed", task_id=task["id"], results=len(all_results), promoted=promoted, skipped=skipped)
        return {"task_id": task["id"], "results": len(all_results), "promoted": promoted, "skipped": skipped}

    def run_company_seeds(
        self,
        seeds: list[dict[str, Any]],
        *,
        per_company_limit: int,
        user: dict[str, Any],
        auto_queue: bool = False,
    ) -> dict[str, Any]:
        if not seeds:
            return {"companies": 0, "tasks": [], "results": 0, "promoted": 0, "skipped": 0, "phone_attached": 0, "auto_queue": auto_queue}
        tasks: list[dict[str, Any]] = []
        totals = {"results": 0, "promoted": 0, "skipped": 0, "phone_attached": 0}
        for seed in seeds:
            phone_candidates = list(seed.get("phone_candidates") or [])
            seed_domain = self.domain_resolver.resolve(
                seed.get("company_name"),
                seed.get("company_domain") or seed.get("website"),
                location=seed.get("location"),
                category=seed.get("category") or seed.get("industry"),
            )
            if seed_domain:
                seed["company_domain"] = seed_domain
            channels = find_public_company_channels(seed.get("company_domain") or seed.get("website") or "")
            if not seed.get("phone") and not phone_candidates:
                phone_candidates = channels["phones"]
            seed["public_channels"] = channels
            seed["company_email_candidates"] = channels["emails"]
            criteria = company_seed_to_search_criteria(seed)
            result = self.run(criteria, per_company_limit, user=user)
            result["company_name"] = seed.get("company_name")
            result["company_domain"] = seed.get("company_domain")
            tasks.append(result)
            totals["results"] += int(result.get("results") or 0)
            totals["promoted"] += int(result.get("promoted") or 0)
            totals["skipped"] += int(result.get("skipped") or 0)
            if seed.get("phone") or phone_candidates:
                totals["phone_attached"] += self.repo.update_contacts_phone_from_search_task(
                    int(result["task_id"]),
                    phone=seed.get("phone"),
                    phone_candidates=phone_candidates,
                )
        return {"companies": len(seeds), "tasks": tasks, "results": totals["results"], "promoted": totals["promoted"], "skipped": totals["skipped"], "phone_attached": totals["phone_attached"], "auto_queue": auto_queue}

    def promote_result(self, result_id: int, *, user: dict[str, Any]) -> dict[str, Any]:
        result = self.repo.get_lead_search_result_for_user(result_id, user)
        if not result:
            raise RuntimeError("Search result not found")
        contact = _contact_from_search_result(result, result["task_id"])
        inserted, skipped = self.repo.upsert_contacts([contact], pool_type="public")
        promoted_contact = self.repo.get_contact_by_linkedin_url(result["linkedin_url"])
        if promoted_contact:
            self.repo.mark_lead_search_result_promoted(result_id, promoted_contact["id"])
        return {"inserted": inserted, "skipped": skipped, "contact_id": promoted_contact["id"] if promoted_contact else None}

    def adopt_candidate(self, contact_id: int, email: str, *, user: dict[str, Any]) -> dict[str, Any]:
        contact = self.repo.get_private_contact_for_user(contact_id, user)
        if not contact:
            raise RuntimeError("Contact not found")
        candidates = contact.get("email_candidates") or []
        selected = next((item for item in candidates if str(item.get("email", "")).lower() == email.lower()), None)
        if not selected:
            raise RuntimeError("Email candidate not found")
        if selected.get("category") != "personal_work":
            raise RuntimeError("Only personal work email candidates can be adopted")
        self.repo.adopt_email_candidate(contact_id, selected)
        return {"contact_id": contact_id, "email": selected["email"]}

    def _verify_high_confidence_candidates(self, contact: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        threshold = int(self.cfg.get("auto_verify_threshold") or 70)
        max_count = int(self.cfg.get("max_paid_verifications_per_contact") or 2)
        if int(contact.get("lead_score") or 0) < threshold:
            return
        hunter_key = self.config.apis.get("hunter_key", "")
        prospeo_key = self.config.apis.get("prospeo_key", "")
        hunter = HunterClient(hunter_key) if hunter_key else None
        prospeo = ProspeoClient(prospeo_key) if prospeo_key else None
        checked = 0
        for item in sorted(candidates, key=lambda row: int(row.get("confidence") or 0), reverse=True):
            if checked >= max_count:
                break
            if item.get("category") != "personal_work" or int(item.get("confidence") or 0) < threshold:
                continue
            checked += 1
            email = item.get("email")
            if hunter and is_full_email(email):
                verified = hunter.verify_email(email)
                status = verified.get("status", "unknown")
                score = int(verified.get("score") or item.get("confidence") or 0)
                self.repo.record_email_provider_stat("hunter_verify_candidate", calls=1, candidates=1, valid_candidates=1 if status == "valid" else 0, selected=1 if status == "valid" else 0, credits_used=1)
                if status == "valid":
                    self.repo.adopt_email_candidate(contact["id"], {**item, "status": "valid", "confidence": max(score, int(item.get("confidence") or 0)), "source": "linkedin_public_search+hunter_verify"})
                    return
            if prospeo:
                try:
                    found = prospeo.enrich_person(contact)
                except Exception:
                    found = {}
                email_obj = found.get("email") or found.get("work_email")
                found_email = email_obj.get("email") if isinstance(email_obj, dict) else email_obj
                self.repo.record_email_provider_stat("prospeo_candidate", calls=1, candidates=1, valid_candidates=1 if is_full_email(found_email) else 0, selected=1 if is_full_email(found_email) else 0, credits_used=1)
                if is_full_email(found_email):
                    self.repo.adopt_email_candidate(contact["id"], EmailCandidate.build(found_email, "linkedin_public_search+prospeo", "valid", 95, "personal_work").__dict__)
                    return


def build_linkedin_queries(criteria: dict[str, Any]) -> list[str]:
    name = _target_name(criteria)
    role = _clean(criteria.get("role") or criteria.get("title"))
    role_keywords = regional_role_terms(criteria)
    industry = _clean(criteria.get("industry"))
    location = _clean(criteria.get("location"))
    company = _clean(criteria.get("company_keyword") or criteria.get("company") or criteria.get("company_website"))
    parts = [part for part in [name, role, industry, location, company] if part]
    queries = []
    if name and company:
        queries.append(f'site:linkedin.com/in "{name}" "{company}"')
    if name and role:
        queries.append(f'site:linkedin.com/in "{name}" "{role}"')
    if name and location:
        queries.append(f'site:linkedin.com/in "{name}" "{location}"')
    if parts:
        queries.append("site:linkedin.com/in " + " ".join(f'"{part}"' for part in parts))
    if role and industry:
        queries.append(f'site:linkedin.com/in "{role}" "{industry}"')
    if role and location:
        queries.append(f'site:linkedin.com/in "{role}" "{location}"')
    if role and company:
        queries.append(f'site:linkedin.com/in "{role}" "{company}"')
    for title in role_keywords[:10]:
        title_parts = [part for part in [title, industry, location, company] if part]
        if title_parts:
            queries.append("site:linkedin.com/in " + " ".join(f'"{part}"' for part in title_parts))
    if not queries and role:
        queries.append(f'site:linkedin.com/in "{role}"')
    return list(dict.fromkeys(queries or ["site:linkedin.com/in"]))


def build_search_client(config: AppConfig) -> FallbackSearchClient | None:
    key = config.apis.get("google_cse_key", "")
    cse_id = config.apis.get("google_cse_id", "")
    tavily_key = config.apis.get("tavily_key", "")
    brave_key = config.apis.get("brave_search_key", "")
    clients: list[tuple[str, SearchClient]] = []
    if brave_key:
        clients.append(("brave_search", BraveSearchClient(brave_key)))
    if tavily_key:
        clients.append(("tavily", TavilySearchClient(tavily_key)))
    if key and cse_id:
        clients.append(("google_cse", GoogleCSEClient(key, cse_id)))
    return FallbackSearchClient(clients) if clients else None


def company_seed_to_search_criteria(seed: dict[str, Any]) -> dict[str, Any]:
    titles = seed.get("job_titles") or []
    if isinstance(titles, str):
        titles = [item.strip() for item in re.split(r"[,;，；]", titles) if item.strip()]
    role_keywords = titles[:10] if titles else ["founder", "owner", "partner", "director", "head"]
    role = role_keywords[0]
    return {
        "role": role,
        "title": role,
        "role_keywords": role_keywords,
        "industry": seed.get("industry") or seed.get("category") or "",
        "location": seed.get("location") or "",
        "company_keyword": seed.get("company_name") or seed.get("company_domain") or "",
        "company_website": seed.get("company_domain") or seed.get("website") or "",
        "seed_reason": seed.get("reason") or "",
        "seed_category": seed.get("category") or "",
        "public_channels": seed.get("public_channels") or {},
        "company_email_candidates": seed.get("company_email_candidates") or [],
        "regional_profile": detect_regional_profile(seed.get("location"), seed.get("category"), seed.get("industry")).key,
        "auto_domain_lookup": True,
        "auto_generate_email_candidates": True,
        "high_confidence_verify": True,
    }


def parse_linkedin_search_item(item: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any] | None:
    url = _normalize_linkedin_url(item.get("link") or item.get("formattedUrl") or "")
    if not url:
        return None
    title = _clean_title(item.get("title") or "")
    snippet = _clean(item.get("snippet") or "")
    title_parts = [part.strip() for part in re.split(r"\s[-|]\s", title) if part.strip()]
    name = title_parts[0] if title_parts else ""
    if not name or any(token.lower() in name.lower() for token in ("jobs", "company", "linkedin")):
        return None
    first_name, last_name = _split_name(name)
    job_title = title_parts[1] if len(title_parts) > 1 else criteria.get("role") or criteria.get("title")
    company_name = title_parts[2] if len(title_parts) > 2 else _extract_company_from_snippet(snippet)
    return {
        "raw_title": title,
        "raw_snippet": snippet,
        "raw_url": item.get("link") or "",
        "linkedin_url": url,
        "first_name": first_name,
        "last_name": last_name,
        "job_title": job_title,
        "company_name": company_name,
        "industry": _clean(criteria.get("industry")),
        "location": _clean(criteria.get("location")),
        "source_context": _source_context_from_criteria(criteria),
        "source": "linkedin_public_search",
    }


def score_lead(parsed: dict[str, Any], criteria: dict[str, Any]) -> int:
    return score_lead_details(parsed, criteria)[0]


def score_lead_details(parsed: dict[str, Any], criteria: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    evidence: list[dict[str, Any]] = []
    target_name = _target_name(criteria)
    role = _clean(criteria.get("role") or criteria.get("title")).lower()
    industry = _clean(criteria.get("industry")).lower()
    location = _clean(criteria.get("location")).lower()
    company = _clean(criteria.get("company_keyword") or criteria.get("company") or criteria.get("company_name")).lower()
    expected_domain = _domain_from_website(criteria.get("company_website") or "")
    haystack = " ".join(str(parsed.get(key) or "") for key in ("raw_title", "raw_snippet", "job_title", "company_name", "location")).lower()
    observed_name = " ".join(part for part in [str(parsed.get("first_name") or ""), str(parsed.get("last_name") or "")] if part).strip()
    if target_name:
        matched = _names_match(target_name, observed_name)
        evidence.append(_match_evidence("name", target_name, observed_name, matched, 40))
        if matched:
            score += 40
    company_matched = False
    observed_domain = _domain_from_website(parsed.get("company_domain") or "")
    if expected_domain:
        company_matched = observed_domain == expected_domain
        evidence.append(_match_evidence("company_domain", expected_domain, observed_domain, company_matched, 25))
    elif company:
        observed_company = _clean(parsed.get("company_name")).lower()
        company_matched = bool(observed_company and (company in observed_company or observed_company in company or company in haystack))
        evidence.append(_match_evidence("company", company, observed_company, company_matched, 25))
    elif parsed.get("company_name"):
        company_matched = True
    if company_matched:
        score += 25
    role_matched = bool(role and role in haystack)
    if role:
        evidence.append(_match_evidence("title", role, parsed.get("job_title"), role_matched, 15))
    if role_matched:
        score += 15
    industry_matched = bool(industry and industry in haystack)
    if industry:
        evidence.append(_match_evidence("industry", industry, parsed.get("industry") or parsed.get("raw_snippet"), industry_matched, 5))
    if industry_matched:
        score += 5
    location_matched = bool(location and location in haystack)
    if location:
        evidence.append(_match_evidence("location", location, parsed.get("location") or parsed.get("raw_snippet"), location_matched, 10))
    if location_matched:
        score += 10
    if _normalize_linkedin_url(parsed.get("linkedin_url")):
        score += 5
    if not target_name:
        if parsed.get("first_name") and parsed.get("last_name"):
            score += 20
        if company_matched or parsed.get("company_name"):
            score += 10
        if role_matched:
            score += 15
        if industry_matched:
            score += 10
        if location_matched:
            score += 5
    return min(100, score), evidence


def classify_identity_match(parsed: dict[str, Any], criteria: dict[str, Any]) -> str:
    score = int(parsed.get("match_confidence") or parsed.get("lead_score") or 0)
    evidence = parsed.get("match_evidence") or []
    name_check = next((item for item in evidence if item.get("field") == "name"), None)
    company_check = next((item for item in evidence if item.get("field") in {"company", "company_domain"}), None)
    if name_check and not name_check.get("matched"):
        return "mismatch"
    if score >= 85 and (not name_check or name_check.get("matched")) and (not company_check or company_check.get("matched")):
        return "confirmed"
    if score >= 70:
        return "likely"
    return "review"


def generate_public_search_email_candidates(contact: dict[str, Any], repo: Repository | None = None) -> list[EmailCandidate]:
    domain = _domain_from_website(contact.get("company_domain") or "")
    if not domain:
        return []
    guessed = guess_email_candidates(contact, domain)
    historical = repo.email_patterns_for_domain(domain) if repo else []
    ordered = _apply_historical_patterns(contact, domain, historical) + guessed
    candidates: list[EmailCandidate] = []
    seen: set[str] = set()
    for email in ordered:
        if email in seen or not is_full_email(email) or _is_role_based(email):
            continue
        seen.add(email)
        confidence = 85 if email in ordered[: len(historical)] and historical else 72
        if not _domain_resolves(domain):
            confidence -= 20
        candidates.append(EmailCandidate.build(email, "linkedin_public_search_guess", "unverified", confidence, "personal_work"))
    return candidates[:8]


def _merge_email_candidates(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group or []:
            email = str(item.get("email") or item.get("value") or "").strip().lower()
            if not is_full_email(email):
                continue
            row = dict(item)
            row.pop("type", None)
            row.pop("value", None)
            row["email"] = email
            current = merged.get(email)
            if current is None or int(row.get("confidence") or 0) > int(current.get("confidence") or 0):
                merged[email] = row
    return sorted(merged.values(), key=lambda item: int(item.get("confidence") or 0), reverse=True)[:10]


def find_public_company_phone_candidates(domain_or_url: str, *, limit: int = 5) -> list[dict[str, Any]]:
    return find_public_company_channels(domain_or_url, limit=limit)["phones"]


def find_public_company_channels(domain_or_url: str, *, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    domain = _domain_from_website(domain_or_url or "")
    if not domain or _is_blocked_domain(domain) or not _is_public_domain(domain):
        return {"phones": [], "emails": [], "socials": []}
    phones: dict[str, dict[str, Any]] = {}
    emails: dict[str, dict[str, Any]] = {}
    socials: dict[str, dict[str, Any]] = {}
    urls = [f"https://{domain}{path}" for path in ("", "/contact", "/contact-us", "/about", "/about-us")]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_public_page, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            text = future.result()
            if not text:
                continue
            for candidate in pick_public_phone_candidates(text, source_url=url):
                current = phones.get(candidate["phone"])
                if current is None or int(candidate["confidence"]) > int(current["confidence"]):
                    phones[candidate["phone"]] = candidate
            for candidate in pick_public_channel_candidates(text, source_url=url, company_domain=domain):
                target = emails if candidate["type"] == "email" else socials
                target[candidate["value"].casefold()] = candidate
    return {
        "phones": list(phones.values())[:limit],
        "emails": list(emails.values())[:limit],
        "socials": list(socials.values())[:limit],
    }


def _fetch_public_page(url: str) -> str:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "salesbot-public-channel-discovery/1.0"})
        with urllib.request.urlopen(request, timeout=4) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return ""
            return response.read(300_000).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def pick_public_channel_candidates(text: str, *, source_url: str = "", company_domain: str = "") -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for email in re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text or "", flags=re.I):
        normalized = email.lower().strip(".,;:")
        if not is_full_email(normalized):
            continue
        email_domain = normalized.rsplit("@", 1)[-1]
        expected_domain = _domain_from_website(company_domain or "")
        if expected_domain and email_domain not in PUBLIC_MAILBOX_DOMAINS and email_domain != expected_domain and not email_domain.endswith(f".{expected_domain}"):
            continue
        row = asdict(EmailCandidate.build(normalized, "public_website", "unverified", 45, "company_generic"))
        row.update(type="email", value=normalized, source_url=source_url)
        found[("email", normalized)] = row
    for match in re.findall(r'href=["\']([^"\']+)["\']', text or "", flags=re.I):
        decoded = urllib.parse.unquote(match).strip()
        lower = decoded.casefold()
        channel = ""
        if "wa.me/" in lower or "whatsapp.com/" in lower:
            channel = "whatsapp"
        elif "instagram.com/" in lower:
            channel = "instagram"
        elif "facebook.com/" in lower:
            channel = "facebook"
        if not channel:
            continue
        absolute = urllib.parse.urljoin(source_url, decoded)
        found[(channel, absolute.casefold())] = {
            "type": "social",
            "channel": channel,
            "value": absolute,
            "source": "public_website",
            "source_url": source_url,
            "status": "public",
            "confidence": 75,
        }
    return list(found.values())


def pick_public_phone_candidates(text: str, *, source_url: str = "") -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for raw in re.findall(r'href=["\']tel:([^"\']+)["\']', text or "", flags=re.I):
        phone = _normalize_phone(raw)
        if phone:
            candidates[phone] = _phone_candidate(phone, source_url, confidence=80)
    for match in re.finditer(r"(?:(?:\+|00)\d{1,3}[\s().-]*)?(?:\(?\d{2,5}\)?[\s().-]*){2,5}\d{2,5}", text or ""):
        phone = _normalize_phone(match.group(0))
        if not phone:
            continue
        confidence = 70 if phone.startswith("+") else 55
        current = candidates.get(phone)
        if current is None or confidence > int(current["confidence"]):
            candidates[phone] = _phone_candidate(phone, source_url, confidence=confidence)
    return sorted(candidates.values(), key=lambda item: int(item["confidence"]), reverse=True)


def _contact_from_search_result(parsed: dict[str, Any], task_id: int) -> dict[str, Any]:
    source_context = parsed.get("source_context") if isinstance(parsed.get("source_context"), dict) else {}
    return {
        "linkedin_url": parsed["linkedin_url"],
        "first_name": parsed.get("first_name"),
        "last_name": parsed.get("last_name"),
        "job_title": parsed.get("job_title"),
        "company_name": parsed.get("company_name"),
        "company_domain": parsed.get("company_domain"),
        "industry": parsed.get("industry") or source_context.get("seed_category"),
        "location": parsed.get("location"),
        "email_candidates": parsed.get("email_candidates") or [],
        "lead_score": parsed.get("lead_score"),
        "identity_confidence": parsed.get("match_confidence") or parsed.get("lead_score"),
        "identity_status": parsed.get("match_status") or "review",
        "identity_evidence": parsed.get("match_evidence") or [],
        "search_task_id": task_id,
        "source_context": source_context,
        "source": "linkedin_public_search",
        "status": "new",
    }


def _source_context_from_criteria(criteria: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "seed_company": criteria.get("company_keyword") or criteria.get("company_name"),
        "seed_website": criteria.get("company_website"),
        "seed_reason": criteria.get("seed_reason"),
        "seed_category": criteria.get("seed_category") or criteria.get("industry"),
        "seed_location": criteria.get("location"),
        "target_role": criteria.get("role") or criteria.get("title"),
        "target_name": _target_name(criteria),
        "regional_profile": criteria.get("regional_profile"),
    }
    context: dict[str, Any] = {key: _clean(value) for key, value in mapping.items() if _clean(value)}
    channels = criteria.get("public_channels")
    if isinstance(channels, dict) and any(channels.values()):
        context["public_channels"] = channels
    return context


def _target_name(criteria: dict[str, Any]) -> str:
    return _clean(criteria.get("full_name") or criteria.get("person_name") or criteria.get("name"))


def _names_match(expected: str, observed: str) -> bool:
    expected_tokens = _identity_tokens(expected)
    observed_tokens = _identity_tokens(observed)
    if not expected_tokens or not observed_tokens:
        return False
    return expected_tokens == observed_tokens or expected_tokens.issubset(observed_tokens) or observed_tokens.issubset(expected_tokens)


def _identity_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(token) > 1}


def _match_evidence(field: str, expected: Any, observed: Any, matched: bool, weight: int) -> dict[str, Any]:
    return {
        "field": field,
        "expected": _clean(expected),
        "observed": _clean(observed),
        "matched": bool(matched),
        "weight": int(weight),
    }


def _normalize_linkedin_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urllib.parse.urlparse(value)
    if "linkedin.com" not in parsed.netloc.lower():
        return None
    path = parsed.path.rstrip("/")
    if not path.startswith("/in/") or len(path.split("/")) < 3:
        return None
    return f"https://www.linkedin.com{path}"


def _split_name(name: str) -> tuple[str | None, str | None]:
    clean = re.sub(r"\s+", " ", name).strip()
    parts = [part for part in clean.split(" ") if part]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _clean_title(value: str) -> str:
    return re.sub(r"\s*\|\s*LinkedIn.*$", "", _clean(value), flags=re.I)


def _extract_company_from_snippet(snippet: str) -> str | None:
    patterns = [
        r"\bat\s+([A-Z][A-Za-z0-9&.,' -]{2,60})",
        r"\bCompany:\s*([A-Z][A-Za-z0-9&.,' -]{2,60})",
    ]
    for pattern in patterns:
        match = re.search(pattern, snippet)
        if match:
            return match.group(1).strip(" .,-")
    return None


def _apply_historical_patterns(contact: dict[str, Any], domain: str, patterns: list[str]) -> list[str]:
    first = _email_token(contact.get("first_name"))
    last = _email_token(contact.get("last_name"))
    if not first:
        return []
    values = {"first": first, "last": last or "", "f": first[:1], "l": (last or "")[:1]}
    emails = []
    for pattern in patterns:
        local = pattern.format(**values).strip(".")
        if local and "@" not in local:
            emails.append(f"{local}@{domain}")
    return emails


def infer_email_pattern(email: str, first_name: str | None, last_name: str | None) -> str | None:
    if not is_full_email(email):
        return None
    first = _email_token(first_name)
    last = _email_token(last_name)
    local = email.split("@", 1)[0].lower()
    if first and last:
        if local == f"{first}.{last}":
            return "{first}.{last}"
        if local == f"{first}{last}":
            return "{first}{last}"
        if local == f"{first[0]}.{last}":
            return "{f}.{last}"
        if local == f"{first}{last[0]}":
            return "{first}{l}"
        if local == f"{last}.{first}":
            return "{last}.{first}"
    if first and local == first:
        return "{first}"
    return None


def _email_token(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _is_role_based(email: str) -> bool:
    prefix = email.split("@", 1)[0].lower()
    return prefix in {"info", "sales", "support", "contact", "hello", "admin", "press", "media", "help"}


def _domain_resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, 25)
        return True
    except Exception:
        try:
            socket.getaddrinfo(domain, 80)
            return True
        except Exception:
            return False


def _normalize_phone(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    value = urllib.parse.unquote(value)
    value = re.sub(r"(?:ext|extension|x)\s*\.?\s*\d+\s*$", "", value, flags=re.I).strip()
    has_plus = value.startswith("+")
    digits = re.sub(r"\D", "", value)
    if digits.startswith("00"):
        digits = digits[2:]
        has_plus = True
    if not (8 <= len(digits) <= 15):
        return None
    if len(set(digits)) <= 2:
        return None
    if re.fullmatch(r"(?:19|20)\d{6,12}", digits):
        return None
    if has_plus:
        return f"+{digits}"
    return value.strip(" .,-;:")


def _phone_candidate(phone: str, source_url: str, *, confidence: int) -> dict[str, Any]:
    return {
        "phone": phone,
        "source": "public_website_phone",
        "source_url": source_url,
        "status": "unverified",
        "confidence": max(0, min(100, int(confidence or 0))),
    }


def _is_blocked_domain(domain: str) -> bool:
    domain = domain.lower().removeprefix("www.")
    blocked = {item.removeprefix("www.") for item in BLOCKED_DOMAINS}
    return any(domain == item or domain.endswith(f".{item}") for item in blocked)


def _is_public_domain(domain: str) -> bool:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)}
    except Exception:
        return False
    return bool(addresses) and all(ip_address(address).is_global for address in addresses)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


__all__ = [
    "CompanyDomainResolver",
    "GoogleCSEClient",
    "LinkedInPublicSearchService",
    "build_linkedin_queries",
    "company_seed_to_search_criteria",
    "find_public_company_channels",
    "find_public_company_phone_candidates",
    "generate_public_search_email_candidates",
    "infer_email_pattern",
    "parse_linkedin_search_item",
    "pick_public_phone_candidates",
    "pick_public_channel_candidates",
    "score_lead",
]
