from types import SimpleNamespace

from sales_automation.india_hiring_signals import (
    INDIA_HIRING_PLATFORMS,
    INDIA_HIRING_ROLE_TERMS,
    IndiaHiringSignalService,
    is_india_account,
    platforms_for_india_account,
)
from sales_automation.southeast_asia_hiring_signals import parse_platform_hiring_signals


def test_detects_india_and_uses_expected_platform_order():
    seed = {"location": "Mumbai, India", "industry": "luxury retail"}

    assert is_india_account(seed) is True
    assert [item.label for item in platforms_for_india_account(seed)] == [
        "Naukri",
        "Indeed India",
        "Foundit",
    ]
    assert platforms_for_india_account({"location": "Singapore"}) == []


def test_naukri_parser_requires_matching_company_and_india_role():
    rows = [{
        "title": "Category Head - Luxury Retail - Tata CLiQ Luxury",
        "snippet": "Tata CLiQ Luxury is hiring a category head in Mumbai.",
        "link": "https://www.naukri.com/job-listings-category-head-tata-cliq-luxury-mumbai-123",
        "published_at": "2026-07-20T09:00:00+05:30",
    }]

    signals = parse_platform_hiring_signals(
        rows,
        "Tata CLiQ Luxury",
        platform=INDIA_HIRING_PLATFORMS[0],
        country="IN",
        location="Mumbai, India",
        role_terms=INDIA_HIRING_ROLE_TERMS,
    )

    assert len(signals) == 1
    assert signals[0]["source"] == "naukri"
    assert signals[0]["role_match"] == "category head"
    assert signals[0]["vertical_match"] == "luxury"


def test_service_falls_back_to_indeed_and_records_explainable_signal():
    class Search:
        def __init__(self):
            self.queries = []

        def search(self, query, *, limit=10, **options):
            self.queries.append((query, options))
            if "naukri.com" in query:
                return []
            if "in.indeed.com" in query:
                return [{
                    "title": "Retail Head - Reliance Brands",
                    "snippet": "Reliance Brands is hiring a retail head for luxury stores in Mumbai.",
                    "link": "https://in.indeed.com/viewjob?jk=abc123",
                    "published_at": "2026-07-20T09:00:00+05:30",
                }]
            return []

    search = Search()
    config = SimpleNamespace(raw={"sourcing": {"india_hiring_signals": {}}})
    result = IndiaHiringSignalService(config, search).enrich_seed({
        "company_name": "Reliance Brands",
        "location": "Mumbai, India",
        "industry": "luxury retail",
    })

    assert result["india_hiring_signal_checked"] is True
    assert result["signal_source"] == "india_public_jobs"
    assert result["hiring_signals"][0]["source"] == "indeed_india"
    assert result["expansion_score"] >= 55
    assert "Indeed India" in result["hiring_signal_summary"]
    assert len(search.queries) == 2
    assert all(options["country"] == "IN" for _, options in search.queries)


def test_failure_and_non_india_account_never_block_sourcing():
    class Search:
        def search(self, query, *, limit=10, **options):
            raise RuntimeError("temporary search failure")

    config = SimpleNamespace(raw={"sourcing": {"india_hiring_signals": {}}})
    service = IndiaHiringSignalService(config, Search())
    india = service.enrich_seed({
        "company_name": "Example",
        "location": "Delhi, India",
        "reason": "Existing note.",
    })
    london = service.enrich_seed({
        "company_name": "Example",
        "location": "London",
        "reason": "Existing note.",
    })

    assert india["india_hiring_signal_checked"] is True
    assert india["reason"] == "Existing note."
    assert london["india_hiring_signal_checked"] is True
    assert london["reason"] == "Existing note."
