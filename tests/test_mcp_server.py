from __future__ import annotations

from sales_automation import mcp_server


def test_mcp_module_exposes_server_builder() -> None:
    assert callable(mcp_server.build_server)


def test_compact_contact_returns_stable_public_shape() -> None:
    row = {
        "id": 12,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "email_status": "valid",
        "phone": "+123456789",
        "job_title": "Founder",
        "company_name": "Example Inc",
        "company_domain": "example.com",
        "status": "enriched",
        "sequence_step": 1,
        "lifecycle_stage": "lead",
        "sabcd_stage": "D",
        "pool_type": "private",
        "owner": "April",
        "lead_score": 82,
        "last_contacted_at": None,
        "last_event_type": "opened",
    }

    assert mcp_server._compact_contact(row) == {
        "id": 12,
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "email_status": "valid",
        "phone": "+123456789",
        "job_title": "Founder",
        "company_name": "Example Inc",
        "company_domain": "example.com",
        "status": "enriched",
        "sequence_step": 1,
        "lifecycle_stage": "lead",
        "sabcd_stage": "D",
        "pool_type": "private",
        "owner": "April",
        "lead_score": 82,
        "last_contacted_at": "",
        "last_event_type": "opened",
    }
