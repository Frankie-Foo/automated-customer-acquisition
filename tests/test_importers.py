from sales_automation.importers import parse_company_seed_csv, parse_contacts_csv


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


def test_parse_contacts_csv_maps_phone_candidates():
    contacts = parse_contacts_csv("email,company,phone\nlead@example.com,Example Inc,+1 555 0100\n")
    assert contacts[0]["phone"] == "+1 555 0100"
    assert contacts[0]["phone_candidates"][0]["source"] == "csv_import"


def test_parse_company_seed_csv_maps_chinese_template():
    seeds = parse_company_seed_csv(
        "公司/店铺名称,类别,简短背调（为何匹配Vertu资质和清理 & 调性契合度）,官网/联系链接,职位,电话\n"
        "Luxepolis,二手奢侈品平台,印度二手奢侈品电商,luxepolis.com,\"founder, owner, partner\",+91 12345\n"
    )

    assert seeds[0]["company_name"] == "Luxepolis"
    assert seeds[0]["company_domain"] == "luxepolis.com"
    assert seeds[0]["job_titles"] == ["founder", "owner", "partner"]
    assert seeds[0]["phone_candidates"][0]["phone"] == "+91 12345"

