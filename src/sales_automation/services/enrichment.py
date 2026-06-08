from __future__ import annotations

from typing import Any

from ..clients import HunterClient, NinjaPearClient, ProspeoClient, ProxycurlClient
from ..config import AppConfig
from ..db import Repository
from ..email_discovery import build_email_discovery_engine
from ..logging_utils import log


class EnrichmentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def enrich(self, limit: int) -> tuple[int, int]:
        hunter, ninjapear, prospeo, proxycurl = self._clients()
        ok = failed = 0
        for contact in self.repo.list_for_enrichment(limit):
            try:
                self._enrich_and_save(contact, hunter, proxycurl, ninjapear, prospeo)
                ok += 1
            except Exception as exc:
                self.repo.update_enrichment(contact["id"], {"email_status": "unknown"}, error=str(exc))
                failed += 1
                log("enrich.failed", contact_id=contact["id"], error=str(exc))
        log("enrich.completed", ok=ok, failed=failed)
        return ok, failed

    def enrich_contact(self, contact_id: int) -> dict[str, Any]:
        hunter, ninjapear, prospeo, proxycurl = self._clients()
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise RuntimeError(f"Contact not found: {contact_id}")
        fields = self._enrich_and_save(contact, hunter, proxycurl, ninjapear, prospeo)
        return {"contact_id": contact_id, "fields": fields}

    def _clients(self) -> tuple[HunterClient | None, NinjaPearClient | None, ProspeoClient | None, ProxycurlClient | None]:
        hunter_key = self.config.apis.get("hunter_key", "")
        ninjapear_key = self.config.apis.get("ninjapear_key", "")
        prospeo_key = self.config.apis.get("prospeo_key", "")
        proxycurl_key = self.config.apis.get("proxycurl_key", "")
        hunter = HunterClient(hunter_key) if hunter_key else None
        ninjapear = NinjaPearClient(ninjapear_key) if ninjapear_key else None
        prospeo = ProspeoClient(prospeo_key) if prospeo_key else None
        proxycurl = ProxycurlClient(proxycurl_key) if proxycurl_key else None
        return hunter, ninjapear, prospeo, proxycurl

    def _enrich_and_save(
        self,
        contact: dict[str, Any],
        hunter: HunterClient | None,
        proxycurl: ProxycurlClient | None,
        ninjapear: NinjaPearClient | None,
        prospeo: ProspeoClient | None,
    ) -> dict[str, Any]:
        fields = self._enrich_one(contact, hunter, proxycurl, ninjapear, prospeo)
        note = None if fields.get("email_status") == "valid" else "No verified email found"
        self.repo.update_enrichment(contact["id"], fields, error=note)
        return fields

    def _enrich_one(
        self,
        contact: dict[str, Any],
        hunter: HunterClient | None,
        proxycurl: ProxycurlClient | None,
        ninjapear: NinjaPearClient | None,
        prospeo: ProspeoClient | None,
    ) -> dict[str, Any]:
        domain = contact.get("company_domain")
        fields = build_email_discovery_engine(self.config, stats_recorder=self.repo.record_email_provider_stat).discover(contact, domain)
        if proxycurl and domain:
            company = proxycurl.company_lookup(domain)
            fields["company_size"] = company.get("company_size") or company.get("employee_count")
            fields["company_funding"] = company.get("funding_data") or company.get("funding_stage")
            fields["industry"] = company.get("industry") or contact.get("industry")
        return fields or {"email_status": "unknown"}

__all__ = ["EnrichmentService"]
