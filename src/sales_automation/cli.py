from __future__ import annotations

import argparse
from pathlib import Path

from .clients import SlackClient
from .config import load_config
from .db import Database, Repository
from .logging_utils import log
from .services import EnrichmentService, OutreachService, QueueService, SchedulerService, SourcingService, WebhookService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="salesbot")
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default="config.yaml")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", parents=[config_parent])

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

    queue = sub.add_parser("queue", parents=[config_parent])
    queue.add_argument("--limit", type=int, default=100)

    send = sub.add_parser("send", parents=[config_parent])
    send.add_argument("--limit", type=int, default=100)

    scheduler = sub.add_parser("scheduler", parents=[config_parent])
    scheduler.add_argument("--enrich-limit", type=int, default=100)
    scheduler.add_argument("--queue-limit", type=int, default=100)
    scheduler.add_argument("--send-limit", type=int, default=100)

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

    args = parser.parse_args(argv)
    config = load_config(args.config)
    repo = Repository(Database(config))

    if args.command == "migrate":
        applied = repo.db.migrate()
        log("migrate.completed", applied=applied)
    elif args.command == "source":
        criteria = {
            "title": args.title or args.role,
            "role": args.role or args.title,
            "company_website": args.company_website,
            "industry": args.industry,
            "location": args.location,
            "company_size": args.company_size,
        }
        SourcingService(config, repo).source(criteria, args.limit)
    elif args.command == "enrich":
        EnrichmentService(config, repo).enrich(args.limit)
    elif args.command == "queue":
        QueueService(repo).queue(args.limit)
    elif args.command == "send":
        OutreachService(config, repo).send_due(args.limit)
    elif args.command == "scheduler":
        SchedulerService(config, repo).run_once(args.enrich_limit, args.queue_limit, args.send_limit)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
