from __future__ import annotations

import argparse
import secrets
import string
from pathlib import Path

from sales_automation.config import load_config
from sales_automation.db import Database, Repository


SALES_ACCOUNTS = [
    {"username": "Frank", "display_name": "Frank", "reply_to_email": "frank.fu@vertu.com"},
    {"username": "Vivi", "display_name": "Vivi", "reply_to_email": "vivien.wang@vertu.cn"},
    {"username": "Viki", "display_name": "Viki", "reply_to_email": "Viki.you@vertu.cn"},
    {"username": "Chen", "display_name": "Chen", "reply_to_email": "Tony.Santoso@vertu.cn"},
    {"username": "April", "display_name": "April", "reply_to_email": "april.yang@vertu.cn"},
    {"username": "Gao", "display_name": "Gao", "reply_to_email": "mark.gao@vertu.cn"},
    {"username": "Henry", "display_name": "Henry", "reply_to_email": "henry.li@vertu.cn"},
    {"username": "Haiwen", "display_name": "Haiwen", "reply_to_email": "Haiwen.he@vertu.cn"},
    {"username": "Ivan", "display_name": "Ivan", "reply_to_email": "ivan.yu@vertu.com"},
    {"username": "Yubing", "display_name": "Yubing", "reply_to_email": "ivan.yu@vertu.com"},
    {"username": "Safae", "display_name": "Safae", "reply_to_email": "safae@vertu.com"},
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure production sales accounts without resetting existing passwords.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--source-limit", type=int, default=100)
    parser.add_argument("--send-limit", type=int, default=200)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    config = load_config(args.config)
    repo = Repository(Database(config))
    existing = {user["username"].lower(): user for user in repo.list_users()}

    updated: list[str] = []
    created: list[tuple[str, str, str]] = []
    for account in SALES_ACCOUNTS:
        user = existing.get(account["username"].lower())
        if user:
            repo.update_user(
                int(user["id"]),
                display_name=account["display_name"],
                role="sales",
                daily_source_limit=args.source_limit,
                daily_send_limit=args.send_limit,
                reply_to_email=account["reply_to_email"],
                active=True,
            )
            updated.append(account["username"])
            continue

        password = _temporary_password()
        repo.create_user(
            username=account["username"],
            password=password,
            display_name=account["display_name"],
            role="sales",
            daily_source_limit=args.source_limit,
            daily_send_limit=args.send_limit,
            reply_to_email=account["reply_to_email"],
            must_change_password=True,
        )
        created.append((account["username"], password, account["reply_to_email"]))

    lines = [
        f"updated: {', '.join(updated) if updated else 'none'}",
        f"created: {', '.join(item[0] for item in created) if created else 'none'}",
    ]
    if created:
        lines.append("")
        lines.append("temporary passwords, first login requires change:")
        lines.extend(f"{username}\t{password}\t{reply_to}" for username, password, reply_to in created)
    output = "\n".join(lines)
    print(output)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")


def _temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(20))


if __name__ == "__main__":
    main()
