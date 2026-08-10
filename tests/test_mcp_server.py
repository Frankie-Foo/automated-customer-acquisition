from __future__ import annotations

from sales_automation import mcp_server


class _Repo:
    def get_contact_for_user(self, contact_id, user):
        return {"id": contact_id, "pool_type": "public"}

    def get_private_contact_for_user(self, contact_id, user):
        return None


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


def test_mcp_mutations_reject_readable_public_contacts() -> None:
    try:
        mcp_server._require_private_contact(_Repo(), 12, {"id": 2, "role": "sales"})
    except ValueError as exc:
        assert str(exc) == "Contact must be claimed before mutation"
    else:
        raise AssertionError("MCP mutation accepted a public-pool contact")
