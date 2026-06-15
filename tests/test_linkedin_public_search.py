from sales_automation.linkedin_public_search import (
    CompanyDomainResolver,
    build_linkedin_queries,
    company_seed_to_search_criteria,
    generate_public_search_email_candidates,
    parse_linkedin_search_item,
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
