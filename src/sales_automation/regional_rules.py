from __future__ import annotations

import re
from typing import Any

MIDDLE_EAST_LOCATION_KEYWORDS = {
    "abu dhabi",
    "bahrain",
    "doha",
    "dubai",
    "iran",
    "iraq",
    "jeddah",
    "ksa",
    "kuwait",
    "lebanon",
    "middle east",
    "muscat",
    "oman",
    "qatar",
    "riyadh",
    "saudi",
    "tehran",
    "mena",
    "uae",
}

MIDDLE_EAST_DOMAIN_RULES: list[tuple[tuple[str, ...], str]] = [
    (("chalhoub",), "chalhoubgroup.com"),
    (("almalki", "al malki"), "almalki.com"),
    (("al tayer",), "altayer.com"),
    (("alshaya",), "alshaya.com"),
    (("trafalgar",), "trafalgarluxurygroup.com"),
    (("quintessentially",), "quintessentially.com"),
    (("seddiqi",), "seddiqi.com"),
    (("l'azurde", "lazurde"), "lazurde.com"),
    (("alghanim",), "alghanim.com"),
    (("majid al futtaim",), "majidalfuttaim.com"),
    (("four seasons",), "fourseasons.com"),
    (("ritz-carlton", "st. regis", "st regis", "edition", "jw marriott", "marriott"), "marriott.com"),
    (("waldorf", "hilton"), "hilton.com"),
    (("rosewood",), "rosewoodhotels.com"),
    (("park hyatt", "grand hyatt", "hyatt"), "hyatt.com"),
    (("shangri-la", "shangri la"), "shangri-la.com"),
    (("fairmont",), "fairmont.com"),
    (("kempinski",), "kempinski.com"),
    (("jumeirah",), "jumeirah.com"),
    (("intercontinental", "ihg"), "ihg.com"),
    (("anantara",), "anantara.com"),
    (("radisson",), "radissonhotels.com"),
    (("chedi",), "ghmhotels.com"),
    (("jetex", "tashreef"), "jetex.com"),
    (("boudl",), "boudl.com"),
    (("red sea", "shebara"), "redseaglobal.com"),
    (("six senses",), "sixsenses.com"),
    (("khimji",), "khimji.com"),
    (("royal opera house muscat", "opera galleria"), "rohmuscat.org.om"),
    (("oman air",), "omanair.com"),
    (("mall of oman",), "mallofoman.com"),
    (("the avenues",), "the-avenues.com"),
    (("360 mall",), "360mall.com"),
    (("al kout", "tamdeen"), "tamdeen.com"),
]

DIRECTORY_OR_LOW_QUALITY_DOMAINS = {
    "booking.com",
    "com-hotel.website",
    "expedia.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "staravenue.com.my",
    "top100golfcourses.com",
    "tripadvisor.com",
    "visitsaudi.com",
    "wikipedia.org",
    "zoominfo.com",
}


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def is_middle_east_context(*values: Any) -> bool:
    text = " ".join(normalize_text(value) for value in values)
    return any(keyword in text for keyword in MIDDLE_EAST_LOCATION_KEYWORDS)


def mapped_middle_east_domain(company_name: Any, *, location: Any = "", category: Any = "") -> str | None:
    text = " ".join(normalize_text(value) for value in (company_name, location, category))
    if not text:
        return None
    for needles, domain in MIDDLE_EAST_DOMAIN_RULES:
        if any(needle in text for needle in needles):
            return domain
    return None


def is_low_quality_domain(domain: Any) -> bool:
    normalized = normalize_text(domain).removeprefix("www.")
    return normalized in DIRECTORY_OR_LOW_QUALITY_DOMAINS
