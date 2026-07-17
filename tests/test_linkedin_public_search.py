from sales_automation.linkedin_public_search import (
    BraveSearchClient,
    CompanyDomainResolver,
    FallbackSearchClient,
    LinkedInPublicSearchService,
    TavilySearchClient,
    build_linkedin_queries,
    company_seed_to_search_criteria,
    pick_public_channel_candidates,
    generate_public_search_email_candidates,
    parse_linkedin_search_item,
    pick_public_phone_candidates,
    score_lead,
    score_lead_details,
    classify_identity_match,
)
from sales_automation.clients import _domain_from_website


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


def test_exact_person_query_and_scoring_require_name_and_company_match():
    criteria = {
        "full_name": "Ada Lovelace",
        "company_website": "example.com",
        "company_keyword": "Example",
        "role": "Founder",
        "location": "United Kingdom",
    }
    queries = build_linkedin_queries(criteria)
    parsed = {
        "raw_title": "Ada Lovelace - Founder - Example",
        "raw_snippet": "Founder at Example in United Kingdom",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "job_title": "Founder",
        "company_name": "Example",
        "company_domain": "example.com",
        "location": "United Kingdom",
        "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
    }

    score, evidence = score_lead_details(parsed, criteria)
    parsed.update(match_confidence=score, match_evidence=evidence)

    assert '"Ada Lovelace"' in queries[0]
    assert score >= 85
    assert classify_identity_match(parsed, criteria) == "confirmed"
    assert {item["field"] for item in evidence} >= {"name", "company_domain", "title", "location"}


def test_exact_person_name_mismatch_is_rejected_even_with_matching_company():
    criteria = {"full_name": "Ada Lovelace", "company_website": "example.com", "role": "Founder"}
    parsed = {
        "raw_title": "Grace Hopper - Founder - Example",
        "raw_snippet": "Founder at Example",
        "first_name": "Grace",
        "last_name": "Hopper",
        "job_title": "Founder",
        "company_name": "Example",
        "company_domain": "example.com",
        "linkedin_url": "https://www.linkedin.com/in/grace-hopper",
    }
    score, evidence = score_lead_details(parsed, criteria)
    parsed.update(match_confidence=score, match_evidence=evidence)

    assert classify_identity_match(parsed, criteria) == "mismatch"


def test_resolved_seed_domain_is_not_treated_as_observed_company_evidence():
    criteria = {
        "company_keyword": "Raintree Hotels",
        "company_website": "raintreehotels.com",
        "role": "Founder",
        "industry": "hotel",
        "location": "India",
    }
    parsed = {
        "raw_title": "Chengyi Dai - Chengdu, Sichuan, China | Professional Profile",
        "raw_snippet": "Chinese startup and NFT cofounder based in Chengdu.",
        "first_name": "Chengyi",
        "last_name": "Dai",
        "job_title": "Chengdu, Sichuan, China",
        "company_name": "Professional Profile",
        "company_domain": "raintreehotels.com",
        "company_domain_source": "resolved",
        "observed_company_domain": "",
        "industry": "hotel",
        "location": "India",
        "linkedin_url": "https://www.linkedin.com/in/chengyi-dai",
    }

    score, evidence = score_lead_details(parsed, criteria)
    parsed.update(match_confidence=score, match_evidence=evidence)

    assert next(item for item in evidence if item["field"] == "company_domain")["matched"] is False
    assert classify_identity_match(parsed, criteria) == "mismatch"


def test_unverified_email_candidate_cannot_be_adopted():
    class Repo:
        adopted = None

        def get_private_contact_for_user(self, contact_id, user):
            return {
                "id": contact_id,
                "email_candidates": [{
                    "email": "ada@example.com",
                    "category": "personal_work",
                    "status": "accept_all",
                    "confidence": 72,
                }],
            }

        def adopt_email_candidate(self, contact_id, selected):
            self.adopted = (contact_id, selected)

    class Config:
        apis = {}
        raw = {"sourcing": {"linkedin_public_search": {}}}

    repo = Repo()
    service = LinkedInPublicSearchService(Config(), repo)

    try:
        service.adopt_candidate(1, "ada@example.com", user={"id": 7, "role": "sales"})
    except RuntimeError as exc:
        assert "verified as valid" in str(exc)
    else:
        raise AssertionError("unverified candidate was adopted")
    assert repo.adopted is None


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

    parsed = parse_linkedin_search_item(
        item,
        {
            "role": "Brand Manager",
            "location": "United States",
            "industry": "luxury resale",
            "company_keyword": "ROLEX",
            "seed_reason": "premium positioning and certified pre-owned relevance",
            "seed_category": "second-hand luxury platform",
        },
    )

    assert parsed["linkedin_url"] == "https://www.linkedin.com/in/darwin-lee"
    assert parsed["first_name"] == "Darwin"
    assert parsed["last_name"] == "Lee"
    assert parsed["job_title"] == "Brand Manager"
    assert parsed["company_name"] == "ROLEX"
    assert parsed["industry"] == "luxury resale"
    assert parsed["source_context"]["seed_company"] == "ROLEX"
    assert parsed["source_context"]["seed_reason"] == "premium positioning and certified pre-owned relevance"
    assert parsed["source_context"]["seed_category"] == "second-hand luxury platform"
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


def test_domain_resolver_uses_middle_east_group_mapping_before_search():
    class Client:
        def search(self, query, limit=5):
            raise AssertionError("mapped Middle East domains should not require search")

    assert CompanyDomainResolver(Client()).resolve("The Ritz-Carlton, Riyadh", location="KSA") == "marriott.com"
    assert CompanyDomainResolver(Client()).resolve("Chalhoub Group", location="Dubai") == "chalhoubgroup.com"


def test_domain_resolver_rejects_low_quality_directory_domains():
    class Client:
        def search(self, query, limit=5):
            return [
                {"link": "https://www.zoominfo.com/c/acme"},
                {"link": "https://www.visitsaudi.com/en/acme"},
                {"link": "https://www.acme-official.com/about"},
            ]

    assert CompanyDomainResolver(Client()).resolve("Acme", existing_domain="zoominfo.com") == "acme-official.com"


def test_domain_normalization_preserves_meaningful_subdomains():
    assert _domain_from_website("https://www.example.com/path") == "example.com"
    assert _domain_from_website("https://invest.gov.kz") == "invest.gov.kz"


def test_tavily_search_maps_results_to_google_shape():
    class Http:
        def request(self, method, url, *, headers=None, json_body=None):
            assert method == "POST"
            assert url == "https://api.tavily.com/search"
            assert headers["Authorization"] == "Bearer tvly-test"
            assert json_body["search_depth"] == "basic"
            return {"results": [{"title": "Ada - Founder | LinkedIn", "url": "https://www.linkedin.com/in/ada", "content": "Founder at Example."}]}

    items = TavilySearchClient("tvly-test", Http()).search("site:linkedin.com/in Ada", limit=1)

    assert items == [{"title": "Ada - Founder | LinkedIn", "snippet": "Founder at Example.", "link": "https://www.linkedin.com/in/ada", "published_at": ""}]


def test_brave_search_maps_results_to_google_shape():
    class Http:
        def request(self, method, url, *, headers=None, json_body=None):
            assert method == "GET"
            assert url.startswith("https://api.search.brave.com/res/v1/web/search?")
            assert headers["X-Subscription-Token"] == "brave-test"
            assert headers["Accept"] == "application/json"
            assert json_body is None
            return {
                "web": {
                    "results": [
                        {
                            "title": "Ada - Founder | LinkedIn",
                            "url": "https://www.linkedin.com/in/ada",
                            "description": "Founder at Example.",
                        }
                    ]
                }
            }

    items = BraveSearchClient("brave-test", Http()).search("site:linkedin.com/in Ada", limit=1)

    assert items == [{"title": "Ada - Founder | LinkedIn", "snippet": "Founder at Example.", "link": "https://www.linkedin.com/in/ada", "published_at": ""}]


def test_brave_search_forwards_regional_options_and_extra_snippets():
    class Http:
        def request(self, method, url, *, headers=None, json_body=None):
            from urllib.parse import parse_qs, urlparse

            params = parse_qs(urlparse(url).query)
            assert params["country"] == ["SA"]
            assert params["search_lang"] == ["ar"]
            assert params["extra_snippets"] == ["true"]
            return {"web": {"results": [{"title": "نتيجة", "url": "https://example.com", "description": "وصف", "extra_snippets": ["واتساب"]}]}}

    items = BraveSearchClient("brave-test", Http()).search("متجر فاخر", country="SA", search_lang="ar", extra_snippets=True)

    assert items[0]["snippet"] == "وصف واتساب"


def test_brave_search_falls_back_to_all_for_unsupported_country():
    class Http:
        def request(self, method, url, *, headers=None, json_body=None):
            from urllib.parse import parse_qs, urlparse

            assert parse_qs(urlparse(url).query)["country"] == ["ALL"]
            return {"web": {"results": []}}

    assert BraveSearchClient("brave-test", Http()).search("luxury UAE", country="AE") == []


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


def test_linkedin_public_search_uses_configured_fallback_order():
    class Config:
        apis = {"google_cse_key": "expired-google", "google_cse_id": "cx", "tavily_key": "tvly-test"}
        raw = {"sourcing": {"linkedin_public_search": {}}}

    service = LinkedInPublicSearchService(Config(), repo=None)

    assert [name for name, _client in service.client.clients] == ["tavily", "google_cse"]


def test_linkedin_public_search_prefers_brave_when_available():
    class Config:
        apis = {"google_cse_key": "google", "google_cse_id": "cx", "tavily_key": "tvly", "brave_search_key": "brave"}
        raw = {"sourcing": {"linkedin_public_search": {}}}

    service = LinkedInPublicSearchService(Config(), repo=None)

    assert [name for name, _client in service.client.clients] == ["brave_search", "tavily", "google_cse"]


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


def test_public_channel_candidates_keep_generic_email_and_social_links():
    candidates = pick_public_channel_candidates(
        '<a href="mailto:info@example.ae">Email</a>'
        '<a href="https://wa.me/971501234567">WhatsApp</a>'
        '<a href="https://instagram.com/example">Instagram</a>',
        source_url="https://example.ae/contact",
        company_domain="example.ae",
    )

    email = next(item for item in candidates if item["type"] == "email")
    socials = {item["channel"] for item in candidates if item["type"] == "social"}
    assert email["email"] == "info@example.ae"
    assert email["category"] == "company_generic"
    assert email["status"] == "unverified"
    assert email["confidence"] == 45
    assert socials == {"whatsapp", "instagram"}


def test_public_channel_candidates_reject_unrelated_asset_emails():
    candidates = pick_public_channel_candidates(
        "info@example.ae sprite@icons.png owner@gmail.com",
        company_domain="example.ae",
    )

    assert {item["email"] for item in candidates if item["type"] == "email"} == {"info@example.ae", "owner@gmail.com"}
