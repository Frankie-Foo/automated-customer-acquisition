from types import SimpleNamespace

from sales_automation.southeast_asia_hiring_signals import (
    PLATFORMS,
    SoutheastAsiaHiringSignalService,
    build_platform_hiring_query,
    detect_sales_vertical,
    parse_platform_hiring_signals,
    platforms_for_account,
    score_hiring_signals,
    southeast_asia_country,
)


def test_detects_only_the_six_configured_southeast_asia_markets():
    assert southeast_asia_country({"location": "Singapore"}) == "SG"
    assert southeast_asia_country({"location": "Kuala Lumpur, Malaysia"}) == "MY"
    assert southeast_asia_country({"location": "Bangkok, Thailand"}) == "TH"
    assert southeast_asia_country({"location": "Jakarta, Indonesia"}) == "ID"
    assert southeast_asia_country({"location": "Ho Chi Minh City, Vietnam"}) == "VN"
    assert southeast_asia_country({"location": "Manila, Philippines"}) == "PH"
    assert southeast_asia_country({"location": "Phnom Penh, Cambodia"}) is None


def test_platform_route_is_country_and_vertical_specific():
    singapore = platforms_for_account({"location": "Singapore", "industry": "luxury watches"})
    indonesia = platforms_for_account({"location": "Indonesia", "industry": "consumer electronics"})

    assert [item.label for item in singapore[:3]] == ["JobStreet", "MyCareersFuture", "Luxury Careers"]
    assert [item.label for item in indonesia[:3]] == ["JobStreet", "Glints", "Dealls"]
    assert detect_sales_vertical({"industry": "automotive dealer"}) == "automotive"


def test_platform_parser_requires_job_page_company_and_role_and_rejects_expired():
    rows = [
        {
            "title": "Client Advisor at Apple | JobStreet",
            "snippet": "Apple luxury retail team in Singapore is hiring.",
            "link": "https://sg.jobstreet.com/job/123",
            "published_at": "2026-07-20T09:00:00+08:00",
        },
        {
            "title": "Store Manager at Other Brand | JobStreet",
            "snippet": "Other Brand retail role in Singapore.",
            "link": "https://sg.jobstreet.com/job/124",
        },
        {
            "title": "Retail Manager at Apple | JobStreet",
            "snippet": "This job is no longer available.",
            "link": "https://sg.jobstreet.com/job/125",
        },
        {
            "title": "Apple company profile",
            "snippet": "Client Advisor roles and company reviews.",
            "link": "https://sg.jobstreet.com/companies/apple",
        },
    ]

    signals = parse_platform_hiring_signals(
        rows,
        "Apple",
        platform=PLATFORMS["jobstreet_sg"],
        country="SG",
        location="Singapore",
    )

    assert len(signals) == 1
    assert signals[0]["source"] == "jobstreet"
    assert signals[0]["role_match"] == "client advisor"
    assert signals[0]["vertical_match"] == "luxury"
    assert build_platform_hiring_query("Apple", PLATFORMS["jobstreet_sg"]) == 'site:sg.jobstreet.com "Apple"'


def test_local_language_frontline_role_counts_as_expansion_signal():
    rows = [{
        "title": "Promotor Smartphone Samsung - Jakarta | Glints",
        "snippet": "Samsung membuka lowongan promotor untuk electronics retail.",
        "link": "https://glints.com/id/opportunities/jobs/promotor-smartphone/abc",
        "published_at": "2026-07-20T09:00:00+07:00",
    }]

    signals = parse_platform_hiring_signals(
        rows,
        "Samsung",
        platform=PLATFORMS["glints"],
        country="ID",
        location="Jakarta, Indonesia",
    )

    assert signals[0]["role_match"] == "promotor"
    assert score_hiring_signals(signals, location="Jakarta", industry="consumer electronics") >= 55


def test_company_job_listing_page_and_opening_volume_count_as_signal():
    rows = [{
        "title": "Lowongan Kerja Erajaya di Indonesia - Juli 2026 | JobStreet",
        "snippet": "Temukan 269 Erajaya pekerjaan di Indonesia dan berbagai pekerjaan baru setiap hari.",
        "link": "https://id.jobstreet.com/id/Erajaya-jobs",
    }]

    signals = parse_platform_hiring_signals(
        rows,
        "Erajaya",
        platform=PLATFORMS["jobstreet_id"],
        country="ID",
        location="Jakarta, Indonesia",
    )

    assert signals[0]["role_match"] == "multiple_openings"
    assert signals[0]["openings_count"] == 269
    assert score_hiring_signals(signals, location="Jakarta", industry="consumer electronics retail") >= 65


def test_opening_count_never_treats_page_year_as_vacancy_volume():
    rows = [{
        "title": "Lowongan Kerja Erajaya - Juni 2026 | JobStreet",
        "snippet": "Temukan 26 Erajaya pekerjaan di Jakarta Raya.",
        "link": "https://id.jobstreet.com/id/Erajaya-jobs/in-Jakarta-Raya",
    }]

    signals = parse_platform_hiring_signals(
        rows,
        "Erajaya",
        platform=PLATFORMS["jobstreet_id"],
        country="ID",
    )

    assert signals[0]["openings_count"] == 26


def test_service_runs_automatically_and_preserves_explainable_evidence():
    class Search:
        def __init__(self):
            self.queries = []

        def search(self, query, *, limit=10, **options):
            self.queries.append((query, options))
            if "sg.jobstreet.com" not in query:
                return []
            return [{
                "title": "Client Advisor at Apple | JobStreet",
                "snippet": "Apple premium retail hiring in Singapore.",
                "link": "https://sg.jobstreet.com/job/123",
                "published_at": "2026-07-20T09:00:00+08:00",
            }]

    search = Search()
    config = SimpleNamespace(raw={"sourcing": {"southeast_asia_hiring_signals": {}}})
    result = SoutheastAsiaHiringSignalService(config, search).enrich_seed({
        "company_name": "Apple",
        "location": "Singapore",
        "industry": "luxury consumer electronics",
    })

    assert result["southeast_asia_hiring_signal_checked"] is True
    assert result["signal_source"] == "southeast_asia_public_jobs"
    assert result["expansion_score"] >= 55
    assert result["hiring_signals"][0]["source_url"] == "https://sg.jobstreet.com/job/123"
    assert "may be building" in result["hiring_signal_summary"]
    assert all(options["country"] == "SG" for _, options in search.queries)
    assert len(search.queries) == 1


def test_service_failure_or_non_target_country_never_blocks_sourcing():
    class Search:
        def search(self, query, *, limit=10, **options):
            raise RuntimeError("temporary search failure")

    config = SimpleNamespace(raw={"sourcing": {"southeast_asia_hiring_signals": {}}})
    service = SoutheastAsiaHiringSignalService(config, Search())
    singapore = service.enrich_seed({"company_name": "Example", "location": "Singapore", "reason": "Existing note."})
    london = service.enrich_seed({"company_name": "Example", "location": "London", "reason": "Existing note."})

    assert singapore["reason"] == "Existing note."
    assert singapore["southeast_asia_hiring_signal_checked"] is True
    assert london["reason"] == "Existing note."
    assert london["southeast_asia_hiring_signal_checked"] is True
