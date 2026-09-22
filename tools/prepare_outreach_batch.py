"""Save reviewed research and drafts through the application; never approve or send."""
import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sales_automation.config import load_config
from sales_automation.db import Database, Repository
from sales_automation.services.outreach import PersonalizedEmailService
from sales_automation.services.outreach_batches import OutreachBatchService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--batch", required=True, type=int)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    db = Database(config)
    db.bind_system_actor()
    repo = Repository(db)
    with db.connect() as conn:
        owner = conn.execute("SELECT id,username,display_name,role FROM sales_users WHERE lower(username)=lower(%s) AND active", (args.owner,)).fetchone()
    if not owner:
        raise ValueError("Active owner not found")
    db.bind_actor(owner)
    batch = OutreachBatchService(repo).detail(args.batch, user=owner)["batch"]
    items = json.loads(args.input.read_text(encoding="utf-8"))
    report = []
    for item in items:
        contact_id = int(item["contact_id"])
        with db.connect() as conn:
            member = conn.execute("SELECT 1 FROM leads WHERE contact_id=%s AND campaign_id=%s", (contact_id, batch["id"])).fetchone()
        if not member or not repo.get_private_contact_for_user(contact_id, owner):
            raise PermissionError("Contact must belong to both batch and owner")
        if not item.get("sources") or not item.get("summary") or not item.get("subject") or not item.get("body"):
            raise ValueError("Sources, research summary, subject and body are required")
        if not args.apply:
            report.append({"contact_id": contact_id, "state": "dry_run"})
            continue
        repo.upsert_contact_research(contact_id, summary=item["summary"],
            company_signals=item["sources"], person_signals=[], news_signals=[],
            sources=item["sources"], provider="reviewed_public_sources",
            expires_at=datetime.now(UTC) + timedelta(days=7))
        draft = PersonalizedEmailService(config, repo).draft(contact_id, user=owner,
            campaign_id=batch["id"], mode="custom", custom_subject=item["subject"], custom_body=item["body"])
        report.append({"contact_id": contact_id, "state": "draft",
                       "quality": draft["quality_review"]["status"],
                       "blocking_issues": draft["quality_review"].get("blocking_issues", [])})
    print(json.dumps({"batch_id": batch["id"], "results": report, "sent": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
