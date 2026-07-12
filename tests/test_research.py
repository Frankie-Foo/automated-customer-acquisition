from types import SimpleNamespace

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
