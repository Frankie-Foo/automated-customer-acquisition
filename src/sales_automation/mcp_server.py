from __future__ import annotations

import argparse
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .db import Database, Repository
from .importers import parse_company_seed_csv
from .linkedin_public_search import LinkedInPublicSearchService
from .quotas import QuotaService
from .services import LifecycleService, PersonalizedEmailService, ProfileAgentService


def build_server(config_path: str = "config.yaml") -> FastMCP:
    config = load_config(config_path)
    repo = Repository(Database(config))
    app = FastMCP("sales-automation")

    def resolve_user(username: str | None = None) -> dict[str, Any]:
        target = username or os.environ.get("SALESBOT_MCP_USERNAME") or "admin"
        for user in repo.list_users():
            if str(user.get("username") or "").lower() == str(target).lower():
                if not user.get("active", True):
                    raise ValueError(f"User is disabled: {target}")
                return user
        raise ValueError(f"Unknown sales user: {target}")

    @app.tool()
    def search_customers(
        query: str = "",
        status: str = "",
        filter_key: str = "",
        limit: int = 20,
        user_username: str = "",
    ) -> dict[str, Any]:
        """Search CRM contacts scoped to a sales user. Returns compact customer rows."""
        user = resolve_user(user_username or None)
        rows = repo.list_contacts(
            status=status or None,
            search=query or None,
            filter_key=filter_key or None,
            limit=max(1, min(100, int(limit or 20))),
            user=user,
        )
        return {
            "count": len(rows),
            "customers": [_compact_contact(row) for row in rows],
        }

    @app.tool()
    def get_customer_detail(contact_id: int, user_username: str = "") -> dict[str, Any]:
        """Get one customer detail, including candidates, lifecycle, profile and email events."""
        user = resolve_user(user_username or None)
        detail = repo.contact_detail(int(contact_id), user=user)
        if not detail:
            raise ValueError("Contact not found or not accessible")
        return detail

    @app.tool()
    def import_and_source_leads(
        csv_text: str,
        default_location: str = "",
        default_industry: str = "",
        per_company_limit: int = 3,
        user_username: str = "",
    ) -> dict[str, Any]:
        """Import company seed CSV text and run public LinkedIn sourcing. Does not send emails."""
        user = resolve_user(user_username or None)
        seeds = parse_company_seed_csv(csv_text, default_location=default_location, default_industry=default_industry)
        requested = max(1, min(10, int(per_company_limit or 3))) * max(1, len(seeds))
        quotas = QuotaService(config, repo).snapshot(user)
        remaining = min(int(quotas["source"]["remaining_user"]), int(quotas["source"]["remaining_global"]))
        if requested > remaining:
            raise RuntimeError(f"Source quota insufficient: need {requested}, remaining {remaining}")
        result = LinkedInPublicSearchService(config, repo).run_company_seeds(
            seeds,
            per_company_limit=max(1, min(10, int(per_company_limit or 3))),
            user=user,
            auto_queue=False,
        )
        quota_result = QuotaService(config, repo).consume(user, "source", int(result.get("results") or 0))
        batch_report = repo.company_seed_batch_report(
            [int(item["task_id"]) for item in result.get("tasks", []) if item.get("task_id")],
            user=user,
        )
        return {
            "parsed_companies": len(seeds),
            "result": result,
            "batch_report": batch_report,
            "usage": quota_result["user_usage"],
        }

    @app.tool()
    def generate_outreach_email(contact_id: int, user_username: str = "") -> dict[str, Any]:
        """Generate a personalized outreach email draft for a customer. Does not send."""
        user = resolve_user(user_username or None)
        contact = repo.get_contact_for_user(int(contact_id), user)
        if not contact:
            raise ValueError("Contact not found or not accessible")
        draft = PersonalizedEmailService(config, repo).draft(int(contact_id), mode="ai")
        return {
            "contact": _compact_contact(contact),
            "draft": draft,
        }

    @app.tool()
    def update_customer_stage(
        contact_id: int,
        lifecycle_stage: str = "",
        sabcd_stage: str = "",
        disposition: str = "",
        next_action_at: str = "",
        notes: str = "",
        lost_reason: str = "",
        user_username: str = "",
    ) -> dict[str, Any]:
        """Update lifecycle/SABCD stage for a customer scoped to the selected user."""
        user = resolve_user(user_username or None)
        contact = repo.get_contact_for_user(int(contact_id), user)
        if not contact:
            raise ValueError("Contact not found or not accessible")
        result = LifecycleService(repo).update(
            int(contact_id),
            lifecycle_stage=lifecycle_stage or None,
            sabcd_stage=sabcd_stage or None,
            disposition=disposition or None,
            next_action_at=next_action_at or None,
            notes=notes or None,
            lost_reason=lost_reason or None,
        )
        return result

    @app.tool()
    def generate_customer_profile(contact_id: int, user_username: str = "") -> dict[str, Any]:
        """Generate or refresh AI customer profile insights for a customer."""
        user = resolve_user(user_username or None)
        contact = repo.get_contact_for_user(int(contact_id), user)
        if not contact:
            raise ValueError("Contact not found or not accessible")
        return ProfileAgentService(config, repo).summarize(int(contact_id))

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="salesbot-mcp")
    parser.add_argument("--config", default=os.environ.get("SALESBOT_CONFIG", "config.yaml"))
    args = parser.parse_args(argv)
    build_server(args.config).run()
    return 0


def _compact_contact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": " ".join([str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()]).strip(),
        "email": row.get("email"),
        "email_status": row.get("email_status"),
        "phone": row.get("phone"),
        "job_title": row.get("job_title"),
        "company_name": row.get("company_name"),
        "company_domain": row.get("company_domain"),
        "status": row.get("status"),
        "sequence_step": row.get("sequence_step"),
        "lifecycle_stage": row.get("lifecycle_stage"),
        "sabcd_stage": row.get("sabcd_stage"),
        "pool_type": row.get("pool_type"),
        "owner": row.get("owner"),
        "lead_score": row.get("lead_score"),
        "last_contacted_at": str(row.get("last_contacted_at") or ""),
        "last_event_type": row.get("last_event_type"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
