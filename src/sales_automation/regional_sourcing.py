from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RegionalSearchProfile:
    key: str
    label: str
    country: str | None
    search_languages: tuple[str, ...]
    role_terms: tuple[str, ...]
    channel_terms: tuple[str, ...]


GLOBAL_PROFILE = RegionalSearchProfile(
    key="global",
    label="Global",
    country=None,
    search_languages=("en",),
    role_terms=(),
    channel_terms=("official website", "contact", "distributor", "dealer"),
)

_COUNTRY_PROFILES: dict[str, RegionalSearchProfile] = {}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _register(
    aliases: tuple[str, ...],
    *,
    key: str,
    label: str,
    country: str,
    languages: tuple[str, ...],
    role_terms: tuple[str, ...],
    channel_terms: tuple[str, ...],
) -> None:
    profile = RegionalSearchProfile(key, label, country, languages, role_terms, channel_terms)
    for alias in aliases:
        _COUNTRY_PROFILES[_normalize(alias)] = profile


_MENA_ROLES = (
    "owner", "founder", "general manager", "commercial director",
    "مدير عام", "المدير التجاري", "مالك",
)
_MENA_CHANNELS = ("official website", "contact", "WhatsApp", "Instagram", "وكيل", "موزع", "متجر فاخر")
_CENTRAL_ASIA_ROLES = (
    "owner", "founder", "general director", "commercial director",
    "владелец", "основатель", "генеральный директор", "коммерческий директор",
)
_CENTRAL_ASIA_CHANNELS = ("официальный сайт", "контакты", "дистрибьютор", "дилер", "Instagram", "WhatsApp")
_RUSSIA_ROLES = (
    "owner", "founder", "general director", "commercial director", "retail director",
    "владелец", "основатель", "генеральный директор", "коммерческий директор", "директор по рознице",
)
_RUSSIA_CHANNELS = ("официальный сайт", "контакты", "дистрибьютор", "дилер", "магазин", "Telegram")
_SOUTHEAST_ASIA_ROLES = (
    "owner", "founder", "managing director", "general manager", "commercial director", "retail director",
    "pemilik", "pendiri", "giám đốc", "chủ sở hữu", "กรรมการผู้จัดการ", "เจ้าของ",
)
_SOUTHEAST_ASIA_CHANNELS = (
    "official website", "authorized dealer", "distributor", "contact", "WhatsApp", "Instagram", "Facebook", "Zalo",
)
_SOUTH_ASIA_ROLES = ("owner", "founder", "managing director", "business head", "purchase head", "director")
_SOUTH_ASIA_CHANNELS = ("official website", "contact", "authorized dealer", "distributor", "Instagram", "WhatsApp")

for aliases, country, languages in (
    (("united arab emirates", "uae", "dubai", "abu dhabi", "الإمارات"), "AE", ("en", "ar")),
    (("saudi arabia", "saudi", "ksa", "riyadh", "jeddah", "السعودية"), "SA", ("en", "ar")),
    (("qatar", "doha", "قطر"), "QA", ("en", "ar")),
    (("kuwait", "الكويت"), "KW", ("en", "ar")),
    (("bahrain", "البحرين"), "BH", ("en", "ar")),
    (("oman", "muscat", "عمان"), "OM", ("en", "ar")),
    (("iraq", "baghdad", "العراق"), "IQ", ("en", "ar")),
    (("jordan", "amman", "الأردن"), "JO", ("en", "ar")),
    (("lebanon", "beirut", "لبنان"), "LB", ("en", "ar")),
    (("egypt", "cairo", "مصر"), "EG", ("en", "ar")),
    (("iran", "tehran", "ایران"), "IR", ("en", "fa")),
):
    _register(aliases, key="mena", label="Middle East enhanced", country=country, languages=languages, role_terms=_MENA_ROLES, channel_terms=_MENA_CHANNELS)

for aliases, country in (
    (("kazakhstan", "almaty", "astana", "қазақстан", "казахстан"), "KZ"),
    (("uzbekistan", "tashkent", "oʻzbekiston", "узбекистан"), "UZ"),
    (("kyrgyzstan", "bishkek", "кыргызстан", "киргизия"), "KG"),
    (("tajikistan", "dushanbe", "тоҷикистон", "таджикистан"), "TJ"),
    (("turkmenistan", "ashgabat", "туркменистан"), "TM"),
    (("azerbaijan", "baku", "азербайджан"), "AZ"),
    (("armenia", "yerevan", "армения"), "AM"),
    (("georgia", "tbilisi", "грузия"), "GE"),
):
    _register(aliases, key="central_asia", label="Central Asia enhanced", country=country, languages=("en", "ru"), role_terms=_CENTRAL_ASIA_ROLES, channel_terms=_CENTRAL_ASIA_CHANNELS)

_register(
    ("russia", "russian federation", "moscow", "saint petersburg", "st petersburg", "россия", "москва", "санкт-петербург"),
    key="russia",
    label="Russia enhanced",
    country="RU",
    languages=("ru", "en"),
    role_terms=_RUSSIA_ROLES,
    channel_terms=_RUSSIA_CHANNELS,
)

for aliases, country, languages in (
    (("singapore", "新加坡"), "SG", ("en",)),
    (("malaysia", "kuala lumpur", "马来西亚"), "MY", ("en", "ms")),
    (("indonesia", "jakarta", "indonesia", "印度尼西亚"), "ID", ("en", "id")),
    (("thailand", "bangkok", "ประเทศไทย", "泰国"), "TH", ("en", "th")),
    (("vietnam", "ho chi minh", "hanoi", "việt nam", "越南"), "VN", ("en", "vi")),
    (("philippines", "manila", "菲律宾"), "PH", ("en", "tl")),
    (("cambodia", "phnom penh", "កម្ពុជា", "柬埔寨"), "KH", ("en", "km")),
    (("laos", "vientiane", "ລາວ", "老挝"), "LA", ("en", "lo")),
    (("myanmar", "yangon", "မြန်မာ", "缅甸"), "MM", ("en", "my")),
    (("brunei", "bandar seri begawan", "文莱"), "BN", ("en", "ms")),
):
    _register(
        aliases,
        key="southeast_asia",
        label="Southeast Asia enhanced",
        country=country,
        languages=languages,
        role_terms=_SOUTHEAST_ASIA_ROLES,
        channel_terms=_SOUTHEAST_ASIA_CHANNELS,
    )

for aliases, country in (
    (("india", "भारत"), "IN"),
    (("pakistan", "پاکستان"), "PK"),
    (("bangladesh", "বাংলাদেশ"), "BD"),
    (("sri lanka", "ශ්‍රී ලංකාව"), "LK"),
    (("nepal", "नेपाल"), "NP"),
):
    _register(aliases, key="south_asia", label="South Asia enhanced", country=country, languages=("en",), role_terms=_SOUTH_ASIA_ROLES, channel_terms=_SOUTH_ASIA_CHANNELS)

_REGION_ALIASES = {
    "middle east": "mena", "mena": "mena", "gulf": "mena", "gcc": "mena", "中东": "mena",
    "central asia": "central_asia", "中亚": "central_asia",
    "russia": "russia", "俄罗斯": "russia",
    "southeast asia": "southeast_asia", "asean": "southeast_asia", "东南亚": "southeast_asia",
    "south asia": "south_asia", "南亚": "south_asia",
}


def detect_regional_profile(*values: Any) -> RegionalSearchProfile:
    text = _normalize(" ".join(str(value or "") for value in values))
    for alias, profile in _COUNTRY_PROFILES.items():
        if _contains_alias(text, alias):
            return profile
    for alias, key in _REGION_ALIASES.items():
        if _contains_alias(text, alias):
            sample = next((profile for profile in _COUNTRY_PROFILES.values() if profile.key == key), None)
            if sample:
                return RegionalSearchProfile(sample.key, sample.label, None, sample.search_languages, sample.role_terms, sample.channel_terms)
    return GLOBAL_PROFILE


def regional_role_terms(criteria: dict[str, Any], *, limit: int = 12) -> list[str]:
    profile = detect_regional_profile(criteria.get("location"), criteria.get("industry"), criteria.get("seed_category"))
    requested = criteria.get("role_keywords") or []
    if isinstance(requested, str):
        requested = re.split(r"[,;，；]", requested)
    role = criteria.get("role") or criteria.get("title")
    terms = [str(role or "").strip(), *(str(item).strip() for item in requested), *profile.role_terms]
    return list(dict.fromkeys(item for item in terms if item))[:limit]


def search_options(criteria: dict[str, Any]) -> list[dict[str, Any]]:
    profile = detect_regional_profile(criteria.get("location"), criteria.get("industry"), criteria.get("seed_category"))
    return [
        {"country": profile.country, "search_lang": language, "extra_snippets": True}
        for language in profile.search_languages
    ]


def _contains_alias(text: str, alias: str) -> bool:
    if any(ord(char) > 127 for char in alias):
        return alias in text
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None


__all__ = ["GLOBAL_PROFILE", "RegionalSearchProfile", "detect_regional_profile", "regional_role_terms", "search_options"]
