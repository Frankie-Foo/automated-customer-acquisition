from __future__ import annotations

from typing import Any


class ProviderBudgetExceeded(RuntimeError):
    def __init__(self, provider: str, daily_limit: int):
        self.provider = provider
        self.daily_limit = daily_limit
        super().__init__(f"provider_daily_budget_exhausted:{provider}:0/{daily_limit}")


class ProviderBudgetGateway:
    def __init__(self, config: Any, repo: Any):
        self.repo = repo
        discovery = config.raw.get("email_discovery", {})
        self.limits = {
            str(provider): max(0, int(limit))
            for provider, limit in (discovery.get("provider_daily_credit_limits") or {}).items()
        }

    def try_reserve(self, provider: str, amount: int = 1, daily_limit: int | None = None) -> bool:
        limit = self.limits.get(provider) if daily_limit is None else max(0, int(daily_limit))
        if limit is None:
            return True
        return bool(self.repo.reserve_email_provider_credits(provider, max(0, int(amount)), limit))

    def reserve(self, provider: str, amount: int = 1) -> None:
        if not self.try_reserve(provider, amount):
            raise ProviderBudgetExceeded(provider, self.limits[provider])


__all__ = ["ProviderBudgetExceeded", "ProviderBudgetGateway"]
