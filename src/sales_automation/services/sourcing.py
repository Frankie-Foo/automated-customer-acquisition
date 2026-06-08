from __future__ import annotations

from typing import Any

from ..clients import NinjaPearClient, ProspeoClient, ProxycurlClient
from ..config import AppConfig
from ..db import Repository
from ..logging_utils import log


class SourcingService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def source(self, criteria: dict[str, Any], limit: int) -> tuple[int, int]:
        provider = self.config.raw.get("sourcing", {}).get("provider", "prospeo")
        prospeo_key = self.config.apis.get("prospeo_key", "")
        ninjapear_key = self.config.apis.get("ninjapear_key", "")
        if provider == "prospeo" and prospeo_key:
            role = criteria.get("role") or criteria.get("title")
            if not role:
                raise RuntimeError("Prospeo sourcing requires role")
            try:
                contacts = ProspeoClient(prospeo_key).search_people(
                    company_website=criteria.get("company_website"),
                    role=role,
                    industry=criteria.get("industry"),
                    location=criteria.get("location"),
                    limit=limit,
                )
            except RuntimeError as exc:
                if "NO_RESULTS" in str(exc):
                    contacts = []
                else:
                    raise
        elif ninjapear_key:
            company_website = criteria.get("company_website") or criteria.get("company")
            role = criteria.get("role") or criteria.get("title")
            if not company_website or not role:
                raise RuntimeError("NinjaPear sourcing requires company_website and role")
            contacts = NinjaPearClient(ninjapear_key).search_employees(
                company_website=company_website,
                role=role,
                location=criteria.get("location"),
                limit=limit,
            )
        else:
            key = self.config.apis.get("proxycurl_key", "")
            if not key:
                raise RuntimeError("Missing apis.prospeo_key or apis.ninjapear_key")
            contacts = ProxycurlClient(key).search_people(criteria, limit)
        contacts = [contact for contact in contacts if contact.get("linkedin_url")]
        inserted, skipped = self.repo.upsert_contacts(contacts)
        log("source.completed", inserted=inserted, skipped=skipped)
        return inserted, skipped

__all__ = ["SourcingService"]
