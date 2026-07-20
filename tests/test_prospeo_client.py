from sales_automation.clients import ProspeoClient


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_company_only_search_omits_empty_job_title_filter():
    http = FakeHttp({
        "results": [{
            "person": {
                "person_id": "person-1",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "current_job_title": "Managing Director",
                "linkedin_url": "https://www.linkedin.com/in/ada",
            },
            "company": {"name": "Example", "website": "https://example.com"},
        }],
    })
    client = ProspeoClient("secret", http=http)

    people = client.search_people(company_website="example.com", role="", limit=5)

    body = http.calls[0][2]["json_body"]
    assert "person_job_title" not in body["filters"]
    assert people[0]["source_person_id"] == "person-1"
    assert people[0]["job_title"] == "Managing Director"


def test_company_search_accepts_multiple_target_titles():
    http = FakeHttp({"results": []})
    client = ProspeoClient("secret", http=http)

    client.search_people(company_website="example.com", role=["CEO", "Owner"], limit=5)

    body = http.calls[0][2]["json_body"]
    assert body["filters"]["person_job_title"]["include"] == ["CEO", "Owner"]
