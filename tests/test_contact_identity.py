from sales_automation.db import _contact_identity_url


def test_contact_without_linkedin_gets_stable_internal_identity():
    contact = {
        "linkedin_url": None,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "company_name": "Example",
    }

    first = _contact_identity_url(contact)
    second = _contact_identity_url(dict(contact))

    assert first.startswith("urn:contact:")
    assert first == second


def test_real_linkedin_url_is_preserved():
    url = "https://www.linkedin.com/in/ada-lovelace"
    assert _contact_identity_url({"linkedin_url": url}) == url
