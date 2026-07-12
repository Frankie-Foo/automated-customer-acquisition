from __future__ import annotations

import re
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import AppConfig
from ..db import Repository
from ..linkedin_public_search import SearchClient, build_search_client
from ..logging_utils import log


class AccountResearchService:
    def __init__(self, config: AppConfig, repo: Repository, client: SearchClient | None = None):
        self.config = config
        self.repo = repo
        self.client = client or build_search_client(config)

    def research(self, contact_id: int, *, user: dict[str, Any], force: bool = False) -> dict[str, Any]:
        contact = self.repo.get_private_contact_for_user(contact_id, user)
        if not contact:
            raise RuntimeError("Contact not found or not claimed")
        cached = self.repo.get_contact_research(contact_id)
        if cached and not force and _is_fresh(cached.get("expires_at")):
            return cached
        if not self.client:
            raise RuntimeError("Missing research search API: configure BRAVE_SEARCH_API_KEY, TAVILY_API_KEY, or Google CSE")

        company = str(contact.get("company_name") or contact.get("company_domain") or "").strip()
        person = " ".join(part for part in [contact.get("first_name"), contact.get("last_name")] if part).strip()
        location = str(contact.get("location") or "").strip()
        industry = str(contact.get("industry") or "").strip()
        queries: list[tuple[str, str]] = []
        if company:
            queries.append(("company", f'"{company}" {location} company business expansion partnership'))
            queries.append(("news", f'"{company}" latest news announcement {industry}'))
        if person and company:
            queries.append(("person", f'"{person}" "{company}"'))

        signals: dict[str, list[dict[str, Any]]] = {"company": [], "person": [], "news": []}
        seen: set[str] = set()
        for signal_type, query in queries:
            for item in self.client.search(query, limit=5):
                signal = _normalize_signal(item, query=query, signal_type=signal_type)
                if not signal or signal["url"] in seen:
                    continue
                seen.add(signal["url"])
                signals[signal_type].append(signal)

        sources = [item for kind in ("news", "company", "person") for item in signals[kind]][:15]
        provider = str(getattr(self.client, "last_provider", "search"))
        result = self.repo.upsert_contact_research(
            contact_id,
            summary=_research_summary(contact, sources),
            company_signals=signals["company"],
            person_signals=signals["person"],
            news_signals=signals["news"],
            sources=sources,
            provider=provider,
            expires_at=datetime.now(UTC) + timedelta(days=3),
        )
        log("contact_research.completed", contact_id=contact_id, provider=provider, sources=len(sources))
        return result


def _normalize_signal(item: dict[str, Any], *, query: str, signal_type: str) -> dict[str, Any] | None:
    url = str(item.get("link") or item.get("url") or "").strip()
    title = _clean(item.get("title"))
    snippet = _clean(item.get("snippet") or item.get("content") or item.get("description"))
    if not url.startswith(("http://", "https://")) or not title:
        return None
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    if not host or host in {"linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com"}:
        return None
    return {
        "type": signal_type,
        "title": title[:300],
        "snippet": snippet[:1000],
        "url": url,
        "domain": host,
        "published_at": _clean(item.get("published_at"))[:100],
        "query": query[:500],
    }


def _research_summary(contact: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    company = contact.get("company_name") or contact.get("company_domain") or "This account"
    if not sources:
        return f"No current public business or news signals were found for {company}; use only the imported account facts."
    highlights = "; ".join(item["title"] for item in sources[:3])
    return f"Public research for {company} found {len(sources)} source(s). Most relevant titles: {highlights}. Verify each source before using it as a factual claim."


def _is_fresh(value: Any) -> bool:
    if not value:
        return False
    if isinstance(value, datetime):
        current = value
    else:
        try:
            current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current > datetime.now(UTC)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


__all__ = ["AccountResearchService"]
