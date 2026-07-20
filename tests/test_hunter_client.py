from sales_automation.clients import HunterClient


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def request(self, method, url, **kwargs):
        self.urls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_hunter_domain_finder_returns_company_domains():
    http = FakeHttp([{"data": [{"domain": "example.com", "company_name": "Example"}]}])
    client = HunterClient("secret", http=http)

    rows = client.find_company_domains("Example", limit=1)

    assert rows[0]["domain"] == "example.com"
    assert "domain-finder?" in http.urls[0][1]
    assert "perfect_match=true" in http.urls[0][1]


def test_hunter_domain_search_requests_verified_personal_decision_makers():
    http = FakeHttp([{"data": {"domain": "example.com", "emails": [{"value": "ada@example.com"}]}}])
    client = HunterClient("secret", http=http)

    result = client.search_domain_emails(domain="example.com", limit=5)

    assert result["emails"][0]["value"] == "ada@example.com"
    url = http.urls[0][1]
    assert "domain-search?" in url
    assert "type=personal" in url
    assert "verification_status=valid%2Caccept_all" in url
