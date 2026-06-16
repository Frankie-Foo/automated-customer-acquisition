from sales_automation.linkedin_public_search import (
    CompanyDomainResolver,
    FallbackSearchClient,
    TavilySearchClient,
    build_linkedin_queries,
    company_seed_to_search_criteria,
    generate_public_search_email_candidates,
    parse_linkedin_search_item,
    pick_public_phone_candidates,
    score_lead,
)


def test_build_linkedin_queries_uses_public_profile_site_filter():
    queries = build_linkedin_queries({
        "role": "Brand Manager",
        "industry": "luxury",
        "location": "United States",
        "company_keyword": "Rolex",
    })

    assert queries
    assert all(query.startswith("site:linkedin.com/in") for query in queries)
    assert '"Brand Manager"' in queries[0]
    assert '"luxury"' in queries[0]


def test_company_seed_to_search_criteria_expands_job_titles():
    criteria = company_seed_to_search_criteria({
        "company_name": "Luxepolis",
        "company_domain": "luxepolis.com",
        "category": "luxury resale",
        "job_titles": ["founder", "owner", "partner"],
        "location": "India",
    })
    queries = build_linkedin_queries(criteria)

    assert criteria["role"] == "founder"
    assert criteria["role_keywords"] == ["founder", "owner", "partner"]
    assert any('"owner"' in query and '"Luxepolis"' in query for query in queries)


def test_parse_linkedin_profile_filters_non_profile_urls():
    item = {
        "title": "Darwin Lee - Brand Manager - ROLEX | LinkedIn",
        "snippet": "Brand Manager at ROLEX in United States.",
        "link": "https://www.linkedin.com/in/darwin-lee/",
    }

    parsed = parse_linkedin_search_item(item, {"role": "Brand Manager", "location": "United States"})

    assert parsed["linkedin_url"] == "https://www.linkedin.com/in/darwin-lee"
    assert parsed["first_name"] == "Darwin"
    assert parsed["last_name"] == "Lee"
    assert parsed["job_title"] == "Brand Manager"
    assert parsed["company_name"] == "ROLEX"
    assert parse_linkedin_search_item({**item, "link": "https://www.linkedin.com/company/rolex/"}, {}) is None
    assert parse_linkedin_search_item({**item, "link": "https://www.linkedin.com/jobs/view/1/"}, {}) is None


def test_score_lead_counts_role_industry_location_company_and_summary():
    parsed = {
        "raw_title": "Ada Lovelace - Founder - Luxury SaaS",
        "raw_snippet": "Founder in United States building luxury software.",
        "job_title": "Founder",
        "company_name": "Luxury SaaS",
        "location": "United States",
        "linkedin_url": "https://www.linkedin.com/in/ada",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }

    assert score_lead(parsed, {"role": "Founder", "industry": "luxury", "location": "United States"}) == 100


def test_domain_resolver_excludes_non_official_domains():
    class Client:
        def search(self, query, limit=5):
            return [
                {"link": "https://www.linkedin.com/company/acme"},
                {"link": "https://en.wikipedia.org/wiki/Acme"},
                {"link": "https://www.acme.com/about"},
            ]

    assert CompanyDomainResolver(Client()).resolve("Acme") == "acme.com"


def test_tavily_search_maps_results_to_google_shape():
    class Http:
        def request(self, method, url, *, headers=None, json_body=None):
            assert method == "POST"
            assert url == "https://api.tavily.com/search"
            assert headers["Authorization"] == "Bearer tvly-test"
            assert json_body["search_depth"] == "basic"
            return {"results": [{"title": "Ada - Founder | LinkedIn", "url": "https://www.linkedin.com/in/ada", "content": "Founder at Example."}]}

    items = TavilySearchClient("tvly-test", Http()).search("site:linkedin.com/in Ada", limit=1)

    assert items == [{"title": "Ada - Founder | LinkedIn", "snippet": "Founder at Example.", "link": "https://www.linkedin.com/in/ada"}]


def test_fallback_search_uses_second_provider_after_first_fails():
    class Broken:
        def search(self, query, *, limit=10):
            raise RuntimeError("google failed")

    class Working:
        def search(self, query, *, limit=10):
            return [{"title": "ok", "link": "https://www.linkedin.com/in/ok", "snippet": "ok"}]

    client = FallbackSearchClient([("google_cse", Broken()), ("tavily", Working())])

    assert client.search("site:linkedin.com/in ok", limit=1)[0]["title"] == "ok"
    assert client.last_provider == "tavily"


def test_candidate_generation_prefers_historical_patterns(monkeypatch):
    class Repo:
        def email_patterns_for_domain(self, domain):
            return ["{f}.{last}"]

    monkeypatch.setattr("sales_automation.linkedin_public_search._domain_resolves", lambda domain: True)

    candidates = generate_public_search_email_candidates(
        {"first_name": "Ada", "last_name": "Lovelace", "company_domain": "example.com"},
        Repo(),
    )

    assert candidates[0].email == "a.lovelace@example.com"
    assert candidates[0].confidence == 85
    assert all(candidate.category == "personal_work" for candidate in candidates)


def test_public_phone_candidates_prefer_tel_links_and_filter_noise():
    candidates = pick_public_phone_candidates(
        '<a href="tel:+91 98765 43210">Call</a>'
        '<p>Office: +91 98765 43210</p>'
        '<p>Founded 20240101. Zip 10001.</p>',
        source_url="https://luxepolis.com/contact",
    )

    assert candidates[0]["phone"] == "+919876543210"
    assert candidates[0]["source"] == "public_website_phone"
    assert candidates[0]["confidence"] == 80
    assert all("20240101" not in item["phone"] for item in candidates)
