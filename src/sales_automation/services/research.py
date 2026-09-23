from __future__ import annotations

import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from ipaddress import ip_address
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import AppConfig
from ..db import Repository
from ..linkedin_public_search import SearchClient, build_search_client
from ..logging_utils import log
from ..http import _assert_external_url_allowed


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
            try:
                if not self.client:
                    raise RuntimeError("Missing research search API")
                items = self.client.search(query, limit=5)
            except RuntimeError:
                official = _official_source(contact)
                if not official:
                    raise
                signals["company"].append(official)
                break
            for item in items:
                signal = _normalize_signal(item, query=query, signal_type=signal_type)
                if not signal or signal["url"] in seen:
                    continue
                seen.add(signal["url"])
                signals[signal_type].append(signal)

        sources = [item for kind in ("news", "company", "person") for item in signals[kind]][:15]
        if not sources:
            raise RuntimeError("No verifiable public research sources found")
        provider = str(getattr(self.client, "last_provider", "search"))
        if any(item.get("retrieval_method") == "company_website" for item in sources):
            provider = "company_website"
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


class _WebsiteText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


class _CompanyRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, company_domain):
        super().__init__()
        self.company_domain = company_domain.removeprefix("www.")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme != "https" or host != self.company_domain or parsed.port not in (None, 443):
            return None
        _assert_external_url_allowed(newurl)
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ip_address(row[4][0]).is_global for row in addresses):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _official_source(contact):
    domain = str(contact.get("company_domain") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", domain):
        return None
    url = "https://" + domain + "/"
    try:
        _assert_external_url_allowed(url)
        addresses = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ip_address(row[4][0]).is_global for row in addresses):
            return None
        request = urllib.request.Request(url, headers={"User-Agent": "salesbot-account-research/1.0"})
        with urllib.request.build_opener(_CompanyRedirect(domain)).open(request, timeout=12) as response:
            if "text/html" not in response.headers.get("Content-Type", ""):
                return None
            parser = _WebsiteText()
            parser.feed(response.read(300_000).decode("utf-8", errors="replace"))
        text = _clean(" ".join(parser.parts))
        company = str(contact.get("company_name") or "")
        words = [word for word in re.findall(r"[a-z]+", company.lower()) if len(word) > 3]
        if len(text) < 150 or not words or not any(word in text.lower() for word in words):
            return None
        if any(term in text.lower() for term in ("verify you are human", "access denied", "domain for sale")):
            return None
        return {"type": "company", "title": company + " company website", "snippet": text[:4000],
                "url": response.geturl(), "domain": domain, "published_at": "", "query": "",
                "retrieval_method": "company_website", "retrieved_at": datetime.now(UTC).isoformat()}
    except Exception:
        return None


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
