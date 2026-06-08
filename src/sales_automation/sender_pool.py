from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .config import AppConfig
from .db import Repository


class SenderPoolManager:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def pick_sender(self) -> dict[str, Any]:
        accounts = self._configured_accounts()
        if not accounts:
            raise RuntimeError("No sender account configured")
        registered = [self.repo.ensure_sender_account(account) for account in accounts]
        available: list[dict[str, Any]] = []
        for account, row in zip(accounts, registered):
            usage = self.repo.sender_usage_today(int(row["id"]))
            limit = self._effective_limit({**account, **row})
            if int(usage["send_count"] or 0) < limit:
                available.append({**account, **row, "daily_limit": limit, "send_count": int(usage["send_count"] or 0)})
        if not available:
            raise RuntimeError("No sender account available today")
        strategy = self.config.raw.get("sender_pool", {}).get("strategy", "round_robin")
        if strategy == "least_used":
            return sorted(available, key=lambda item: (item["send_count"], item["email"]))[0]
        total = self.repo.sender_total_sent_today()
        return sorted(available, key=lambda item: item["email"])[total % len(available)]

    def record_send(self, sender: dict[str, Any]) -> None:
        sender_id = sender.get("id")
        if sender_id:
            self.repo.record_sender_send(int(sender_id))

    def _configured_accounts(self) -> list[dict[str, Any]]:
        pool = self.config.raw.get("sender_pool", {})
        accounts = [account for account in pool.get("accounts") or [] if account.get("email")]
        if accounts:
            return [
                {
                    "name": account.get("name") or account.get("email"),
                    "email": account["email"],
                    "provider": account.get("provider") or self.config.sender.get("provider", "resend"),
                    "daily_limit": int(account.get("daily_limit") or 100),
                    "warmup_stage": account.get("warmup_stage") or "production",
                    "dry_run": account.get("dry_run", self.config.sender.get("dry_run", True)),
                    "created_at": account.get("created_at"),
                }
                for account in accounts
            ]
        sender = self.config.sender
        if not sender.get("email"):
            return []
        return [
            {
                "name": sender.get("name") or sender.get("email"),
                "email": sender["email"],
                "provider": sender.get("provider", "resend"),
                "daily_limit": int(sender.get("daily_limit") or 100),
                "warmup_stage": "production",
                "dry_run": sender.get("dry_run", True),
            }
        ]

    def _effective_limit(self, account: dict[str, Any]) -> int:
        configured = int(account.get("daily_limit") or 100)
        if account.get("warmup_stage") != "warmup":
            return configured
        created_at = account.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if not isinstance(created_at, datetime):
            return min(configured, 10)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_days = max(0, (datetime.now(UTC) - created_at.astimezone(UTC)).days)
        return min(configured, 10 * (age_days + 1))
