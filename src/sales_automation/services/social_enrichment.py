from __future__ import annotations

from typing import Any

from ..clients import PeopleDataLabsClient, PeopleDBClient
from ..config import AppConfig
from ..db import Repository
from ..logging_utils import log


class SocialEnrichmentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def enrich(self, limit: int) -> tuple[int, int]:
        client, provider = self._client()
        ok = failed = 0
        for contact in self.repo.list_for_social_enrichment(limit):
            if self._enrich_and_save(contact, client, provider):
                ok += 1
            else:
                failed += 1
        log("social_enrich.completed", provider=provider, ok=ok, failed=failed)
        return ok, failed

    def enrich_contact(self, contact_id: int) -> dict[str, Any]:
        client, provider = self._client()
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise RuntimeError(f"Contact not found: {contact_id}")
        ok = self._enrich_and_save(contact, client, provider)
        return {"contact_id": contact_id, "ok": ok, "provider": provider}

    def _client(self) -> tuple[Any, str]:
        peopledb_key = self.config.apis.get("peopledb_key", "")
        pdl_key = self.config.apis.get("pdl_key", "")
        if peopledb_key:
            client: Any = PeopleDBClient(peopledb_key)
            provider = "peopledb"
        elif pdl_key:
            client = PeopleDataLabsClient(pdl_key)
            provider = "pdl"
        else:
            raise RuntimeError("Missing apis.peopledb_key or apis.pdl_key")
        return client, provider

    def _enrich_and_save(self, contact: dict[str, Any], client: Any, provider: str) -> bool:
        try:
            record = client.enrich_person(contact)
            profiles = _extract_social_profiles(record, contact)
            if not profiles:
                self.repo.update_social_profiles(contact["id"], {}, error="No social profiles found")
                return False
            self.repo.update_social_profiles(contact["id"], profiles)
            return True
        except Exception as exc:
            self.repo.update_social_profiles(contact["id"], {}, error=str(exc))
            log("social_enrich.failed", provider=provider, contact_id=contact["id"], error=str(exc))
            return False


def _extract_social_profiles(record: dict[str, Any], contact: dict[str, Any] | None = None) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    direct_fields = {
        "linkedin": ("linkedin_url", "linkedin"),
        "twitter": ("twitter_url", "twitter"),
        "github": ("github_url", "github"),
        "facebook": ("facebook_url", "facebook"),
        "website": ("personal_website", "website"),
    }
    for target, keys in direct_fields.items():
        value = _first_string(record, *keys)
        if value:
            profiles[target] = value
    if contact and str(contact.get("linkedin_url") or "").startswith("http"):
        profiles.setdefault("linkedin", contact["linkedin_url"])
    github_login = _first_string(record, "github_login", "github_username")
    if github_login:
        profiles.setdefault("github", f"https://github.com/{github_login}")
    linkedin_id = _first_string(record, "linkedin_public_identifier", "linkedin_id")
    if linkedin_id and not str(linkedin_id).startswith("http"):
        profiles.setdefault("linkedin", f"https://www.linkedin.com/in/{linkedin_id}")
    twitter_login = _first_string(record, "twitter_login", "twitter_username", "x_login")
    if twitter_login and not str(twitter_login).startswith("http"):
        profiles.setdefault("twitter", f"https://x.com/{twitter_login.lstrip('@')}")
    for item in record.get("profiles") or []:
        if not isinstance(item, dict):
            continue
        network = str(item.get("network") or item.get("type") or "").lower()
        url = item.get("url") or item.get("profile_url") or item.get("id")
        if not url:
            continue
        if "linkedin" in network:
            profiles.setdefault("linkedin", url)
        elif network in {"twitter", "x"} or "twitter" in network:
            profiles.setdefault("twitter", url)
        elif "github" in network:
            profiles.setdefault("github", url)
        elif "facebook" in network:
            profiles.setdefault("facebook", url)
    return {key: value for key, value in profiles.items() if value}


def _first_string(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None

__all__ = ["SocialEnrichmentService", "_extract_social_profiles"]
