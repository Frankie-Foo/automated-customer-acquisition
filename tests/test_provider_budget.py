from types import SimpleNamespace

import pytest

from sales_automation.provider_budget import ProviderBudgetExceeded, ProviderBudgetGateway
from sales_automation.services.sourcing import SourcingService


class Repo:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.reservations = []

    def reserve_email_provider_credits(self, provider, amount, limit):
        self.reservations.append((provider, amount, limit))
        return self.allowed


def config(limits=None):
    return SimpleNamespace(
        raw={
            "email_discovery": {"provider_daily_credit_limits": limits or {}},
            "sourcing": {"provider": "prospeo"},
        },
        apis={"prospeo_key": "test-key"},
    )


def test_gateway_reserves_configured_provider_budget():
    repo = Repo()

    ProviderBudgetGateway(config({"hunter": 20}), repo).reserve("hunter", 2)

    assert repo.reservations == [("hunter", 2, 20)]


def test_gateway_leaves_unconfigured_provider_compatible():
    repo = Repo(allowed=False)

    ProviderBudgetGateway(config(), repo).reserve("hunter")

    assert repo.reservations == []


def test_sourcing_stops_before_paid_api_when_budget_exhausted():
    repo = Repo(allowed=False)

    with pytest.raises(ProviderBudgetExceeded, match="provider_daily_budget_exhausted:prospeo"):
        SourcingService(config({"prospeo": 0}), repo).source(
            {"company_website": "example.com", "role": "Founder"},
            5,
        )

    assert repo.reservations == [("prospeo", 1, 0)]
