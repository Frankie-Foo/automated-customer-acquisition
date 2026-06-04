from sales_automation.importers import parse_contacts_csv


def test_parse_contacts_csv_maps_common_headers():
    contacts = parse_contacts_csv(
        "LinkedIn Profile URL,First Name,Last Name,Work Email,Company,Website,Title\n"
        "https://linkedin.com/in/a,Ada,Lovelace,ada@example.com,Example Inc,https://example.com,CTO\n"
    )
    assert contacts[0]["linkedin_url"] == "https://linkedin.com/in/a"
    assert contacts[0]["email"] == "ada@example.com"
    assert contacts[0]["email_status"] == "valid"
    assert contacts[0]["status"] == "enriched"
    assert contacts[0]["company_domain"] == "example.com"


def test_parse_contacts_csv_creates_synthetic_url_when_missing():
    contacts = parse_contacts_csv("email,company\nlead@example.com,Example Inc\n")
    assert contacts[0]["linkedin_url"].startswith("manual://csv/")

