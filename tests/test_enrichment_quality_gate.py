from pathlib import Path

from sales_automation.config import AppConfig
from sales_automation.services.enrichment import EnrichmentService


class _Repo:
    def list_for_enrichment(self, _limit, *, user=None):
        return [{
            "id": 1,
            "company_name": "Company-only Record",
            "linkedin_url": "https://linkedin.com/company/example",
        }]


def test_automatic_enrichment_skips_disqualified_company_only_record():
    service = EnrichmentService(AppConfig(raw={}, root_dir=Path(".")), _Repo())
    service._clients = lambda: (None, None, None, None)
    service._enrich_and_save = lambda *_args: (_ for _ in ()).throw(AssertionError("must not call providers"))

    assert service.enrich(10) == (0, 0)
