from sales_automation.regional_sourcing import detect_regional_profile, regional_role_terms, search_options


def test_detects_middle_east_country_and_languages():
    profile = detect_regional_profile("Riyadh, Saudi Arabia", "luxury retail")

    assert profile.key == "mena"
    assert profile.country == "SA"
    assert profile.search_languages == ("en", "ar")


def test_detects_central_asia_and_adds_russian_roles():
    profile = detect_regional_profile("Almaty, Kazakhstan")
    terms = regional_role_terms({"role": "owner", "location": "Almaty, Kazakhstan"})

    assert profile.key == "central_asia"
    assert profile.country == "KZ"
    assert "генеральный директор" in terms


def test_detects_south_asia_and_keeps_country_targeting():
    options = search_options({"location": "India", "industry": "five star hotels"})

    assert options == [{"country": "IN", "search_lang": "en", "extra_snippets": True}]


def test_detects_russia_and_prioritizes_russian_search():
    profile = detect_regional_profile("Moscow, Russia")
    terms = regional_role_terms({"role": "owner", "location": "Moscow, Russia"})

    assert profile.key == "russia"
    assert profile.country == "RU"
    assert profile.search_languages == ("ru", "en")
    assert "генеральный директор" in terms
    assert "директор магазина" in terms
    assert "байер" in terms


def test_detects_southeast_asia_and_uses_local_language():
    profile = detect_regional_profile("Ho Chi Minh City, Vietnam")
    options = search_options({"location": "Ho Chi Minh City, Vietnam"})

    assert profile.key == "southeast_asia"
    assert profile.country == "VN"
    assert options == [
        {"country": "VN", "search_lang": "en", "extra_snippets": True},
        {"country": "VN", "search_lang": "vi", "extra_snippets": True},
    ]


def test_detects_each_supported_southeast_asia_country():
    assert detect_regional_profile("Singapore").country == "SG"
    assert detect_regional_profile("Kuala Lumpur, Malaysia").country == "MY"
    assert detect_regional_profile("Bangkok, Thailand").country == "TH"
    assert detect_regional_profile("Jakarta, Indonesia").country == "ID"
    assert detect_regional_profile("Manila, Philippines").country == "PH"


def test_southeast_asia_decision_roles_are_country_specific():
    vietnam_terms = regional_role_terms({"role": "owner", "location": "Vietnam"})
    thailand_terms = regional_role_terms({"role": "owner", "location": "Thailand"})

    assert "giám đốc điều hành" in vietnam_terms
    assert "กรรมการผู้จัดการ" not in vietnam_terms
    assert "กรรมการผู้จัดการ" in thailand_terms
    assert "pemilik" not in thailand_terms


def test_unknown_location_uses_global_fallback():
    profile = detect_regional_profile("United States")

    assert profile.key == "global"
    assert profile.country is None
