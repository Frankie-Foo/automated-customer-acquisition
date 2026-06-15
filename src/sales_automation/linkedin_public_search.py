from __future__ import annotations

import re
import socket
import urllib.parse
from dataclasses import asdict
from typing import Any

from .clients import HunterClient, ProspeoClient, _domain_from_website, is_full_email
from .config import AppConfig
from .db import Repository
from .email_discovery import EmailCandidate, guess_email_candidates
from .http import HttpClient
from .logging_utils import log

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


class GoogleCSEClient:
    def __init__(self, api_key: str, cse_id: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.cse_id = cse_id
        self.http = http or HttpClient(timeout=30)

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        params = {
            "key": self.api_key,
            "cx": self.cse_id,
            "q": query,
            "num": max(1, min(10, int(limit or 10))),
        }
        url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(params)
        data = self.http.request("GET", url)
        return data.get("items") or []


class CompanyDomainResolver:
    def __init__(self, client: GoogleCSEClient | None = None):
        self.client = client

    def resolve(self, company_name: str | None, existing_domain: str | None = None) -> str | None:
        domain = _domain_from_website(existing_domain or "")
        if domain and not _is_blocked_domain(domain):
            return domain
        if not company_name or not self.client:
            return None
        query = f'"{company_name}" official website'
        for item in self.client.search(query, limit=5):
            candidate = _domain_from_website(item.get("link") or "")
            if candidate and not _is_blocked_domain(candidate):
                return candidate
        return None


class LinkedInPublicSearchService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo
        self.cfg = config.raw.get("sourcing", {}).get("linkedin_public_search", {})
        key = config.apis.get("google_cse_key", "")
        cse_id = config.apis.get("google_cse_id", "")
        self.client = GoogleCSEClient(key, cse_id) if key and cse_id else None
        self.domain_resolver = CompanyDomainResolver(self.client)

    def run(self, criteria: dict[str, Any], limit: int, *, user: dict[str, Any]) -> dict[str, Any]:
        if not self.client:
            raise RuntimeError("Missing apis.google_cse_key or apis.google_cse_id")
        max_queries = int(self.cfg.get("max_queries_per_run") or 10)
        min_score = int(self.cfg.get("min_lead_score") or 50)
        task = self.repo.create_lead_search_task(
            criteria=criteria,
            provider="google_cse",
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
            for query in queries:
                remaining = max(0, limit - len(all_results))
                if remaining <= 0:
                    break
                for item in self.client.search(query, limit=min(10, remaining)):
                    parsed = parse_linkedin_search_item(item, criteria)
                    if not parsed or parsed["linkedin_url"] in seen_urls:
                        continue
                    seen_urls.add(parsed["linkedin_url"])
                    if auto_domain_lookup:
                        parsed["company_domain"] = self.domain_resolver.resolve(parsed.get("company_name"), parsed.get("company_domain"))
                    parsed["lead_score"] = score_lead(parsed, criteria)
                    if auto_generate_candidates:
                        parsed["email_candidates"] = [asdict(item) for item in generate_public_search_email_candidates(parsed, self.repo)]
                    status = "candidate" if parsed["lead_score"] >= min_score else "low_score"
                    result = self.repo.create_lead_search_result(task["id"], parsed, status=status)
                    all_results.append(result)
                    if parsed["lead_score"] < min_score:
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
                        owner_user_id=int(user["id"]),
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
            criteria = company_seed_to_search_criteria(seed)
            result = self.run(criteria, per_company_limit, user=user)
            result["company_name"] = seed.get("company_name")
            result["company_domain"] = seed.get("company_domain")
            tasks.append(result)
            totals["results"] += int(result.get("results") or 0)
            totals["promoted"] += int(result.get("promoted") or 0)
            totals["skipped"] += int(result.get("skipped") or 0)
            phone_candidates = seed.get("phone_candidates") or []
            if seed.get("phone") or phone_candidates:
                totals["phone_attached"] += self.repo.update_contacts_phone_from_search_task(
                    int(result["task_id"]),
                    phone=seed.get("phone"),
                    phone_candidates=phone_candidates,
                    owner_user_id=int(user["id"]),
                )
        return {"companies": len(seeds), "tasks": tasks, "results": totals["results"], "promoted": totals["promoted"], "skipped": totals["skipped"], "phone_attached": totals["phone_attached"], "auto_queue": auto_queue}

    def promote_result(self, result_id: int, *, user: dict[str, Any]) -> dict[str, Any]:
        result = self.repo.get_lead_search_result_for_user(result_id, user)
        if not result:
            raise RuntimeError("Search result not found")
        contact = _contact_from_search_result(result, result["task_id"])
        inserted, skipped = self.repo.upsert_contacts([contact], owner_user_id=int(user["id"]))
        promoted_contact = self.repo.get_contact_by_linkedin_url(result["linkedin_url"])
        if promoted_contact:
            self.repo.mark_lead_search_result_promoted(result_id, promoted_contact["id"])
        return {"inserted": inserted, "skipped": skipped, "contact_id": promoted_contact["id"] if promoted_contact else None}

    def adopt_candidate(self, contact_id: int, email: str, *, user: dict[str, Any]) -> dict[str, Any]:
        contact = self.repo.get_contact_for_user(contact_id, user)
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
    role = _clean(criteria.get("role") or criteria.get("title"))
    role_keywords = [_clean(item) for item in (criteria.get("role_keywords") or []) if _clean(item)]
    industry = _clean(criteria.get("industry"))
    location = _clean(criteria.get("location"))
    company = _clean(criteria.get("company_keyword") or criteria.get("company") or criteria.get("company_website"))
    parts = [part for part in [role, industry, location, company] if part]
    queries = []
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
        "location": _clean(criteria.get("location")),
        "source": "linkedin_public_search",
    }


def score_lead(parsed: dict[str, Any], criteria: dict[str, Any]) -> int:
    score = 0
    role = _clean(criteria.get("role") or criteria.get("title")).lower()
    industry = _clean(criteria.get("industry")).lower()
    location = _clean(criteria.get("location")).lower()
    haystack = " ".join(str(parsed.get(key) or "") for key in ("raw_title", "raw_snippet", "job_title", "company_name", "location")).lower()
    if role and role in haystack:
        score += 30
    if industry and industry in haystack:
        score += 20
    if location and location in haystack:
        score += 15
    if parsed.get("company_name"):
        score += 15
    if _normalize_linkedin_url(parsed.get("linkedin_url")):
        score += 10
    if parsed.get("first_name") and parsed.get("last_name") and parsed.get("raw_snippet"):
        score += 10
    return min(100, score)


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


def _contact_from_search_result(parsed: dict[str, Any], task_id: int) -> dict[str, Any]:
    return {
        "linkedin_url": parsed["linkedin_url"],
        "first_name": parsed.get("first_name"),
        "last_name": parsed.get("last_name"),
        "job_title": parsed.get("job_title"),
        "company_name": parsed.get("company_name"),
        "company_domain": parsed.get("company_domain"),
        "location": parsed.get("location"),
        "email_candidates": parsed.get("email_candidates") or [],
        "lead_score": parsed.get("lead_score"),
        "search_task_id": task_id,
        "source": "linkedin_public_search",
        "status": "new",
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


def _is_blocked_domain(domain: str) -> bool:
    domain = domain.lower().removeprefix("www.")
    return domain in {item.removeprefix("www.") for item in BLOCKED_DOMAINS}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


__all__ = [
    "CompanyDomainResolver",
    "GoogleCSEClient",
    "LinkedInPublicSearchService",
    "build_linkedin_queries",
    "company_seed_to_search_criteria",
    "generate_public_search_email_candidates",
    "infer_email_pattern",
    "parse_linkedin_search_item",
    "score_lead",
]
