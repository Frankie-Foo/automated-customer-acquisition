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


def test_unknown_location_uses_global_fallback():
    profile = detect_regional_profile("United States")

    assert profile.key == "global"
    assert profile.country is None
