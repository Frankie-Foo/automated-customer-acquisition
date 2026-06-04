from __future__ import annotations

import csv
import hashlib
import io
from typing import Any


FIELD_ALIASES = {
    "linkedin_url": ["linkedin_url", "linkedin", "linkedin profile", "linkedin profile url", "profile_url", "url"],
    "first_name": ["first_name", "first name", "firstname", "given_name"],
    "last_name": ["last_name", "last name", "lastname", "family_name"],
    "email": ["email", "work_email", "work email", "business_email"],
    "job_title": ["job_title", "job title", "title", "role", "position"],
    "company_name": ["company_name", "company", "company name", "organization", "account"],
    "company_domain": ["company_domain", "domain", "company domain", "website", "company_website"],
    "industry": ["industry", "sector"],
    "location": ["location", "city", "country", "region"],
    "notes": ["notes", "note"],
    "source": ["source"],
}


def parse_contacts_csv(text: str, *, default_source: str = "csv_import") -> list[dict[str, Any]]:
    stream = io.StringIO(text)
    sample = text[:2048]
    dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
    reader = csv.DictReader(stream, dialect=dialect)
    if not reader.fieldnames:
        return []
    mapping = _build_mapping(reader.fieldnames)
    contacts: list[dict[str, Any]] = []
    for row in reader:
        contact: dict[str, Any] = {}
        for target, source in mapping.items():
            value = (row.get(source) or "").strip()
            if value:
                contact[target] = value
        if not contact.get("linkedin_url"):
            contact["linkedin_url"] = _synthetic_url(contact)
        if contact.get("company_domain"):
            contact["company_domain"] = _normalize_domain(contact["company_domain"])
        if contact.get("email"):
            contact["email_status"] = "valid"
            contact.setdefault("status", "enriched")
        contact.setdefault("source", default_source)
        if _has_minimum_identity(contact):
            contacts.append(contact)
    return contacts


def _build_mapping(fieldnames: list[str]) -> dict[str, str]:
    normalized = {_normalize(name): name for name in fieldnames}
    mapping: dict[str, str] = {}
    for target, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if _normalize(alias) in normalized:
                mapping[target] = normalized[_normalize(alias)]
                break
    return mapping


def _normalize(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _normalize_domain(value: str) -> str:
    value = value.strip()
    value = value.removeprefix("https://").removeprefix("http://").split("/")[0]
    return value


def _synthetic_url(contact: dict[str, Any]) -> str:
    seed = "|".join(
        [
            contact.get("email", ""),
            contact.get("first_name", ""),
            contact.get("last_name", ""),
            contact.get("company_name", ""),
            contact.get("company_domain", ""),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"manual://csv/{digest}"


def _has_minimum_identity(contact: dict[str, Any]) -> bool:
    return bool(contact.get("email") or contact.get("company_name") or contact.get("company_domain") or contact.get("linkedin_url"))
