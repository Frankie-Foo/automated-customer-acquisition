from __future__ import annotations

from typing import Any

from .config import AppConfig
from .db import Repository


class QuotaService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def consume(self, user: dict[str, Any], action: str, amount: int) -> dict[str, Any]:
        field = self._field(action)
        user_limit = self._user_limit(user, action)
        global_limit = self._global_limit(action)
        usage = self.repo.consume_user_and_global_quota(int(user["id"]), field, amount, user_limit, global_limit)
        return {
            **usage,
            "remaining_user": max(0, user_limit - int(usage["user_usage"][field] or 0)),
            "remaining_global": max(0, global_limit - int(usage["global_usage"][field] or 0)),
            "user_limit": user_limit,
            "global_limit": global_limit,
        }

    def consume_global(self, action: str, amount: int) -> dict[str, Any]:
        field = self._field(action)
        global_limit = self._global_limit(action)
        usage = self.repo.consume_global_quota(field, amount, global_limit)
        return {
            "global_usage": usage,
            "remaining_global": max(0, global_limit - int(usage[field] or 0)),
            "global_limit": global_limit,
        }

    def remaining_global(self, action: str) -> int:
        field = self._field(action)
        usage = self.repo.global_usage()
        return max(0, self._global_limit(action) - int(usage[field] or 0))

    def snapshot(self, user: dict[str, Any]) -> dict[str, Any]:
        user_usage = self.repo.usage_for_user(int(user["id"]))
        global_usage = self.repo.global_usage()
        return {
            "user_usage": user_usage,
            "global_usage": global_usage,
            "source": self._snapshot_for(user, user_usage, global_usage, "source"),
            "send": self._snapshot_for(user, user_usage, global_usage, "send"),
        }

    def _snapshot_for(self, user: dict[str, Any], user_usage: dict[str, Any], global_usage: dict[str, Any], action: str) -> dict[str, int]:
        field = self._field(action)
        user_limit = self._user_limit(user, action)
        global_limit = self._global_limit(action)
        return {
            "user_limit": user_limit,
            "global_limit": global_limit,
            "remaining_user": max(0, user_limit - int(user_usage[field] or 0)),
            "remaining_global": max(0, global_limit - int(global_usage[field] or 0)),
        }

    def _field(self, action: str) -> str:
        if action == "source":
            return "source_count"
        if action == "send":
            return "send_count"
        raise ValueError(f"Unsupported quota action: {action}")

    def _user_limit(self, user: dict[str, Any], action: str) -> int:
        if action == "source":
            return int(user.get("daily_source_limit") or self.config.raw.get("quotas", {}).get("default_user_daily_source") or 100)
        return int(user.get("daily_send_limit") or self.config.raw.get("quotas", {}).get("default_user_daily_send") or 100)

    def _global_limit(self, action: str) -> int:
        quotas = self.config.raw.get("quotas", {})
        if action == "source":
            return int(quotas.get("global_daily_source_limit") or 500)
        return int(quotas.get("global_daily_send_limit") or 300)
