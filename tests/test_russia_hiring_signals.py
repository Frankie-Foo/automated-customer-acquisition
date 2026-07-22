from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from sales_automation.russia_hiring_signals import (
    HeadHunterPublicClient,
    RussiaHiringSignalService,
    build_public_hiring_query,
    is_russia_account,
    parse_company_hiring_signals,
    parse_public_search_hiring_signals,
    score_hiring_signals,
)


def _vacancy(vacancy_id="1", *, employer="Mercury", title="Директор магазина", city="Москва"):
    return {
        "id": vacancy_id,
        "name": title,
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "published_at": "2026-07-20T09:00:00+0300",
        "area": {"name": city},
        "employer": {"id": "77", "name": employer},
        "snippet": {"requirement": "Опыт в luxury retail", "responsibility": "Развитие бутика"},
    }


def test_headhunter_client_uses_official_public_vacancy_api():
    class Http:
        def request(self, method, url, *, headers=None, json_body=None, retries=None):
            assert method == "GET"
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            assert parsed.path == "/vacancies"
            assert params["text"] == ["Mercury"]
            assert params["search_field"] == ["company_name"]
            assert params["area"] == ["113"]
            assert headers["User-Agent"].startswith("outbound-ops/")
            assert retries == 1
            return {"items": [_vacancy()]}

    rows = HeadHunterPublicClient(Http()).search_company_vacancies("Mercury", limit=5)

    assert rows[0]["employer"]["name"] == "Mercury"


def test_russia_detection_uses_location_or_ru_domain():
    assert is_russia_account({"location": "Moscow, Russia"}) is True
    assert is_russia_account({"company_domain": "example.ru"}) is True
    assert is_russia_account({"location": "Singapore", "company_domain": "example.com"}) is False


def test_company_hiring_signal_filters_wrong_employer_and_scores_expansion():
    rows = [
        _vacancy("1"),
        _vacancy("2", title="Категорийный менеджер", city="Санкт-Петербург"),
        _vacancy("3", employer="Unrelated Company"),
    ]

    signals = parse_company_hiring_signals(rows, "Mercury", location="Moscow, Russia")
    score = score_hiring_signals(signals, location="Moscow, Russia", industry="luxury retail")

    assert len(signals) == 2
    assert {item["job_title"] for item in signals} == {"Директор магазина", "Категорийный менеджер"}
    assert all(item["source"] == "hh.ru" for item in signals)
    assert score >= 80


def test_russia_service_enriches_seed_and_uses_employer_website():
    class Client:
        def search_company_vacancies(self, company_name, *, limit=10):
            assert company_name == "Mercury"
            return [_vacancy("1"), _vacancy("2", title="Байер")]

        def get_employer(self, employer_id):
            assert employer_id == "77"
            return {"site_url": "https://www.mercury.ru"}

    config = SimpleNamespace(raw={"sourcing": {"russia_hiring_signals": {"enabled": True, "official_api_enabled": True}}})
    result = RussiaHiringSignalService(config, Client()).enrich_seed({
        "company_name": "Mercury",
        "location": "Moscow, Russia",
        "industry": "luxury retail",
    })

    assert result["company_domain"] == "mercury.ru"
    assert result["expansion_score"] >= 70
    assert len(result["hiring_signals"]) == 2
    assert "Public hh.ru hiring activity" in result["reason"]
    assert result["signal_source"] == "hh.ru_public_vacancies"


def test_russia_signal_failure_does_not_block_existing_sourcing():
    class Client:
        def search_company_vacancies(self, company_name, *, limit=10):
            raise RuntimeError("temporary hh.ru failure")

        def get_employer(self, employer_id):
            raise AssertionError("not reached")

    config = SimpleNamespace(raw={"sourcing": {"russia_hiring_signals": {"official_api_enabled": True}}})
    seed = {"company_name": "Mercury", "location": "Russia", "reason": "Existing account note."}

    assert RussiaHiringSignalService(config, Client()).enrich_seed(seed)["reason"] == "Existing account note."


def test_public_search_fallback_uses_hh_vacancy_pages_only():
    rows = [
        {
            "title": "Директор магазина Mercury — вакансия в Москве | hh.ru",
            "snippet": "Mercury приглашает директора магазина luxury retail.",
            "link": "https://hh.ru/vacancy/123",
            "published_at": "2026-07-20T09:00:00+03:00",
        },
        {
            "title": "Mercury company page",
            "snippet": "Директор магазина",
            "link": "https://example.com/mercury",
        },
        {
            "title": "Байер Mercury (вакансия в архиве c 13 сентября 2013) | hh.ru",
            "snippet": "Mercury приглашает байера.",
            "link": "https://hh.ru/vacancy/999",
        },
    ]

    signals = parse_public_search_hiring_signals(rows, "Mercury", location="Moscow, Russia")

    assert len(signals) == 1
    assert signals[0]["vacancy_id"] == "123"
    assert signals[0]["source"] == "hh.ru_public_search"
    assert build_public_hiring_query("Mercury") == 'site:hh.ru/vacancy "Mercury"'


def test_public_search_accepts_frontline_retail_roles_as_expansion_evidence():
    signals = parse_public_search_hiring_signals([{
        "title": "Продавец-консультант Mercury — вакансия в Москве | hh.ru",
        "snippet": "Mercury расширяет команду premium boutique retail.",
        "link": "https://hh.ru/vacancy/789",
        "published_at": "2026-07-21T09:00:00+03:00",
    }], "Mercury", location="Moscow, Russia")

    assert len(signals) == 1
    assert signals[0]["role_match"] == "продавец-консультант"


def test_service_automatically_falls_back_to_existing_public_search_provider():
    class DisabledOfficialClient:
        def search_company_vacancies(self, company_name, *, limit=10):
            raise AssertionError("official API is disabled by default")

        def get_employer(self, employer_id):
            raise AssertionError("official API is disabled by default")

    class Search:
        def search(self, query, *, limit=10, **options):
            assert query.startswith('site:hh.ru/vacancy "Mercury"')
            assert options["country"] == "RU"
            assert options["search_lang"] == "ru"
            return [{
                "title": "Байер Mercury — вакансия | hh.ru",
                "snippet": "Mercury ищет байера для premium retail в Москве.",
                "link": "https://hh.ru/vacancy/456",
                "published_at": "2026-07-20T09:00:00+03:00",
            }]

    config = SimpleNamespace(raw={"sourcing": {"russia_hiring_signals": {}}})
    result = RussiaHiringSignalService(config, DisabledOfficialClient(), Search()).enrich_seed({
        "company_name": "Mercury",
        "location": "Moscow, Russia",
        "industry": "luxury retail",
    })

    assert result["hiring_signals"][0]["source_url"] == "https://hh.ru/vacancy/456"
    assert result["expansion_score"] >= 70


def test_non_russian_seed_does_not_call_headhunter():
    class Client:
        def search_company_vacancies(self, company_name, *, limit=10):
            raise AssertionError("hh.ru should only run for Russian accounts")

        def get_employer(self, employer_id):
            raise AssertionError("hh.ru should only run for Russian accounts")

    config = SimpleNamespace(raw={"sourcing": {"russia_hiring_signals": {}}})
    seed = {"company_name": "Example", "location": "Singapore", "company_domain": "example.com"}

    assert RussiaHiringSignalService(config, Client()).enrich_seed(seed) == seed
