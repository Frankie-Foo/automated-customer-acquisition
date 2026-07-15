from __future__ import annotations

import argparse
from pathlib import Path

from .clients import SlackClient
from .config import load_config
from .db import Database, Repository
from .logging_utils import log
from .production import readiness
from .quotas import QuotaService
from .services import EnrichmentService, MailboxReplyService, OutreachService, QueueService, SchedulerService, SocialEnrichmentService, SourcingService, WebhookService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="salesbot")
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default="config.yaml")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", parents=[config_parent])
    doctor = sub.add_parser("doctor", parents=[config_parent])
    doctor.add_argument("--strict", action="store_true", help="Exit non-zero when required production checks are not ready")
    doctor.add_argument("--database-only", action="store_true", help="Only check PostgreSQL connectivity")

    source = sub.add_parser("source", parents=[config_parent])
    source.add_argument("--title")
    source.add_argument("--role")
    source.add_argument("--company-website")
    source.add_argument("--industry", default="")
    source.add_argument("--location", default="")
    source.add_argument("--company-size", default="")
    source.add_argument("--limit", type=int, default=100)

    enrich = sub.add_parser("enrich", parents=[config_parent])
    enrich.add_argument("--limit", type=int, default=100)

    social_enrich = sub.add_parser("social-enrich", parents=[config_parent])
    social_enrich.add_argument("--limit", type=int, default=100)

    queue = sub.add_parser("queue", parents=[config_parent])
    queue.add_argument("--limit", type=int, default=100)

    send = sub.add_parser("send", parents=[config_parent])
    send.add_argument("--limit", type=int, default=100)

    scheduler = sub.add_parser("scheduler", parents=[config_parent])
    scheduler.add_argument("--enrich-limit", type=int, default=100)
    scheduler.add_argument("--queue-limit", type=int, default=100)
    scheduler.add_argument("--send-limit", type=int, default=100)

    mailbox_poll = sub.add_parser("mailbox-poll", parents=[config_parent])
    mailbox_poll.add_argument("--limit", type=int, default=100)

    mark = sub.add_parser("mark", parents=[config_parent])
    mark.add_argument("--contact-id", type=int, required=True)
    mark.add_argument("--status", required=True)
    mark.add_argument("--notes")

    blacklist = sub.add_parser("blacklist", parents=[config_parent])
    blacklist.add_argument("--email")
    blacklist.add_argument("--domain")
    blacklist.add_argument("--reason")

    export = sub.add_parser("export", parents=[config_parent])
    export.add_argument("--status")
    export.add_argument("--out", required=True)

    webhook = sub.add_parser("webhook", parents=[config_parent])
    webhook.add_argument("--provider", required=True)
    webhook.add_argument("--payload", required=True)

    user_add = sub.add_parser("user-add", parents=[config_parent])
    user_add.add_argument("--username", required=True)
    user_add.add_argument("--password", required=True)
    user_add.add_argument("--display-name", required=True)
    user_add.add_argument("--role", default="sales")
    user_add.add_argument("--source-limit", type=int, default=100)
    user_add.add_argument("--send-limit", type=int, default=200)
    user_add.add_argument("--no-force-password-change", action="store_true")

    sub.add_parser("user-list", parents=[config_parent])

    args = parser.parse_args(argv)
    config = load_config(args.config)
    repo = Repository(Database(config))

    if args.command == "migrate":
        applied = repo.db.migrate()
        log("migrate.completed", applied=applied)
    elif args.command == "doctor":
        db_ok = repo.db.is_available()
        if args.database_only:
            log("doctor.database", ok=db_ok)
            return 0 if db_ok else 1
        data = readiness(config)
        checks = [{"name": "database_connection", "ok": db_ok, "required": True, "message": "PostgreSQL connection succeeds"}, *data["checks"]]
        ready = db_ok and all(check["ok"] for check in checks if check.get("required"))
        for check in checks:
            log("doctor.check", name=check["name"], ok=check["ok"], required=check.get("required", True), message=check.get("message"))
        log("doctor.completed", ready=ready)
        if args.strict and not ready:
            return 1
    elif args.command == "source":
        criteria = {
            "title": args.title or args.role,
            "role": args.role or args.title,
            "company_website": args.company_website,
            "industry": args.industry,
            "location": args.location,
            "company_size": args.company_size,
        }
        quota = QuotaService(config, repo)
        limit = min(args.limit, quota.remaining_global("source"))
        inserted, skipped = SourcingService(config, repo).source(criteria, limit)
        quota.consume_global("source", inserted)
        log("quota.global_source_consumed", inserted=inserted, skipped=skipped)
    elif args.command == "enrich":
        EnrichmentService(config, repo).enrich(args.limit)
    elif args.command == "social-enrich":
        SocialEnrichmentService(config, repo).enrich(args.limit)
    elif args.command == "queue":
        QueueService(repo).queue(args.limit)
    elif args.command == "send":
        quota = QuotaService(config, repo)
        limit = min(args.limit, quota.remaining_global("send"))
        sent = OutreachService(config, repo).send_due(limit)
        quota.consume_global("send", sent)
        log("quota.global_send_consumed", sent=sent)
    elif args.command == "scheduler":
        SchedulerService(config, repo).run_once(args.enrich_limit, args.queue_limit, args.send_limit)
    elif args.command == "mailbox-poll":
        stats = MailboxReplyService(config, repo).poll_once(args.limit)
        log("mailbox.poll", **stats)
    elif args.command == "mark":
        repo.mark_status(args.contact_id, args.status, notes=args.notes)
        log("mark.completed", contact_id=args.contact_id, status=args.status)
    elif args.command == "blacklist":
        if not args.email and not args.domain:
            raise SystemExit("Provide --email or --domain")
        repo.add_blacklist(email=args.email, domain=args.domain, reason=args.reason)
        log("blacklist.completed", email=args.email, domain=args.domain)
    elif args.command == "export":
        count = repo.export_contacts(Path(args.out), args.status)
        log("export.completed", count=count, out=args.out)
    elif args.command == "webhook":
        notifier = SlackClient(config.raw.get("notifications", {}).get("slack_webhook_url"))
        WebhookService(repo, notifier).process_file(args.provider, Path(args.payload))
    elif args.command == "user-add":
        user = repo.create_user(
            username=args.username,
            password=args.password,
            display_name=args.display_name,
            role=args.role,
            daily_source_limit=args.source_limit,
            daily_send_limit=args.send_limit,
            must_change_password=not args.no_force_password_change,
        )
        log("user.added", id=user["id"], username=user["username"], role=user["role"])
    elif args.command == "user-list":
        for user in repo.list_users():
            log(
                "user",
                id=user["id"],
                username=user["username"],
                display_name=user["display_name"],
                role=user["role"],
                source_limit=user["daily_source_limit"],
                send_limit=user["daily_send_limit"],
                active=user["active"],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
