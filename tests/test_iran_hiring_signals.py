from types import SimpleNamespace

from sales_automation.iran_hiring_signals import (
    IRAN_HIRING_PLATFORMS,
    IranHiringSignalService,
    build_iran_hiring_query,
    is_iran_account,
    parse_iran_hiring_signals,
    platforms_for_iran_account,
)


def test_detects_iran_from_location_or_company_domain():
    assert is_iran_account({"location": "Tehran, Iran"}) is True
    assert is_iran_account({"company_domain": "example.ir"}) is True
    assert is_iran_account({"location": "Dubai, UAE"}) is False


def test_uses_professional_platforms_before_local_classifieds():
    platforms = platforms_for_iran_account({"location": "Tehran, Iran"})

    assert [item.label for item in platforms[:4]] == [
        "IranTalent",
        "JobVision",
        "Jobinja",
        "Divar Jobs",
    ]
    assert build_iran_hiring_query("Digikala", platforms[0]) == (
        'site:irantalent.com "Digikala" استخدام'
    )


def test_parser_accepts_persian_company_role_and_vertical_terms():
    rows = [{
        "title": "مدیر فروش دیجی‌کالا | JobVision",
        "snippet": "شرکت دیجی‌کالا برای فروشگاه لوکس در تهران مدیر فروش استخدام می‌کند.",
        "link": "https://jobvision.ir/jobs/123",
        "published_at": "2026-07-20T09:00:00+03:30",
    }]

    signals = parse_iran_hiring_signals(
        rows,
        "دیجی‌کالا",
        platform=IRAN_HIRING_PLATFORMS[1],
        country="IR",
        location="Tehran, Iran",
    )

    assert len(signals) == 1
    assert signals[0]["source"] == "jobvision"
    assert signals[0]["role_match"] == "مدیر فروش"
    assert signals[0]["vertical_match"] == "لوکس"


def test_individual_job_rejects_role_term_only_found_in_noisy_snippet():
    rows = [{
        "title": "Planning & Analytics Specialist - Digikala",
        "snippet": "Related vacancies include فروشنده and مدیر فروش.",
        "link": "https://jobvision.ir/jobs/262297/planning-analytics-specialist-digikala",
    }]

    assert parse_iran_hiring_signals(
        rows,
        "Digikala",
        platform=IRAN_HIRING_PLATFORMS[1],
        location="Tehran, Iran",
    ) == []


def test_service_falls_back_to_jobvision_and_records_explainable_signal():
    class Search:
        def __init__(self):
            self.queries = []

        def search(self, query, *, limit=10, **options):
            self.queries.append((query, options))
            if "irantalent.com" in query:
                return []
            if "jobvision.ir" in query:
                return [{
                    "title": "Sales Director at Digikala | JobVision",
                    "snippet": "Digikala is hiring a sales director for premium retail in Tehran.",
                    "link": "https://jobvision.ir/jobs/abc123",
                    "published_at": "2026-07-20T09:00:00+03:30",
                }]
            return []

    search = Search()
    config = SimpleNamespace(raw={"sourcing": {"iran_hiring_signals": {}}})
    result = IranHiringSignalService(config, search).enrich_seed({
        "company_name": "Digikala",
        "location": "Tehran, Iran",
        "industry": "premium consumer electronics retail",
    })

    assert result["iran_hiring_signal_checked"] is True
    assert result["signal_source"] == "iran_public_jobs"
    assert result["hiring_signals"][0]["source"] == "jobvision"
    assert result["expansion_score"] >= 55
    assert "JobVision" in result["hiring_signal_summary"]
    assert len(search.queries) == 2
    assert all(options["country"] == "IR" for _, options in search.queries)
    assert all(options["search_lang"] == "fa" for _, options in search.queries)


def test_search_failure_or_non_iran_account_never_blocks_sourcing():
    class Search:
        def search(self, query, *, limit=10, **options):
            raise RuntimeError("temporary search failure")

    config = SimpleNamespace(raw={"sourcing": {"iran_hiring_signals": {}}})
    service = IranHiringSignalService(config, Search())
    iran = service.enrich_seed({
        "company_name": "Example",
        "location": "Tehran, Iran",
        "reason": "Existing note.",
    })
    london = service.enrich_seed({
        "company_name": "Example",
        "location": "London",
        "reason": "Existing note.",
    })

    assert iran["iran_hiring_signal_checked"] is True
    assert iran["reason"] == "Existing note."
    assert london["iran_hiring_signal_checked"] is True
    assert london["reason"] == "Existing note."
