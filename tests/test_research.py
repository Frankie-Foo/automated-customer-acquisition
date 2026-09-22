from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sales_automation.services import research as module

from sales_automation.services.research import AccountResearchService


class Repo:
    def __init__(self):
        self.saved = None

    def get_private_contact_for_user(self, contact_id, user):
        return {
            "id": contact_id,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "company_name": "Example",
            "company_domain": "example.com",
            "job_title": "Founder",
            "location": "United Kingdom",
            "industry": "luxury technology",
        }

    def get_contact_research(self, contact_id):
        return None

    def upsert_contact_research(self, contact_id, **kwargs):
        self.saved = {"contact_id": contact_id, **kwargs}
        return self.saved


class Search:
    last_provider = "brave_search"

    def search(self, query, *, limit=5):
        if "latest news" in query:
            return [{
                "title": "Example opens a new flagship store",
                "snippet": "The company announced a flagship opening in London.",
                "link": "https://news.example.test/example-store",
                "published_at": "2026-07-09",
            }]
        return [{
            "title": "Example company profile",
            "snippet": "Example operates premium retail locations.",
            "link": "https://example.com/about",
        }]


def test_research_persists_grounded_sources_for_email_generation():
    repo = Repo()
    service = AccountResearchService(SimpleNamespace(apis={}, raw={}), repo, client=Search())

    result = service.research(42, user={"id": 2, "role": "sales"})

    assert result["provider"] == "brave_search"
    assert result["news_signals"][0]["published_at"] == "2026-07-09"
    assert all(item["url"].startswith("https://") for item in result["sources"])
    assert "found" in result["summary"]


def test_search_outage_uses_company_evidence(monkeypatch):
    official = {"type": "company", "title": "Example", "url": "https://example.com/",
                "snippet": "Verified company text", "retrieval_method": "company_website"}
    monkeypatch.setattr(module, "_official_source", lambda contact: official)
    search = Mock()
    search.search.side_effect = RuntimeError("search unavailable")
    result = AccountResearchService(SimpleNamespace(apis={}, raw={}), Repo(), client=search).research(42, user={"id": 2})
    assert result["sources"] == [official]
    assert result["provider"] == "company_website"


def test_outage_without_evidence_does_not_save_research(monkeypatch):
    monkeypatch.setattr(module, "_official_source", lambda contact: None)
    search = Mock()
    search.search.side_effect = RuntimeError("search unavailable")
    repo = Repo()
    with pytest.raises(RuntimeError):
        AccountResearchService(SimpleNamespace(apis={}, raw={}), repo, client=search).research(42, user={"id": 2})
    assert repo.saved is None


def test_official_site_rejects_private_network(monkeypatch):
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *args, **kwargs: [(0, 0, 0, '', ('127.0.0.1', 443))])
    opener = Mock()
    monkeypatch.setattr(module.urllib.request, "build_opener", opener)
    assert module._official_source({"company_domain": "example.com"}) is None
    opener.assert_not_called()
