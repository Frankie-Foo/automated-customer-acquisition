from types import SimpleNamespace

from sales_automation.services.acquisition_planner import AcquisitionPlannerService, plan_combinations
from sales_automation.regional_sourcing import detect_regional_profile


def test_plan_combinations_rotates_region_industry_company_and_role():
    plan = {
        "regions": ["United Kingdom", "India"],
        "industries": ["watches", "luxury cars"],
        "company_types": ["distributor"],
        "role_terms": ["owner"],
        "cursor_position": 1,
        "combinations_per_run": 2,
    }

    assert plan_combinations(plan) == [
        {"location": "United Kingdom", "industry": "luxury cars", "company_type": "distributor", "role": "owner"},
        {"location": "India", "industry": "watches", "company_type": "distributor", "role": "owner"},
    ]


def test_plan_combinations_split_or_roles_into_searchable_titles():
    plan = {
        "regions": ["India"],
        "industries": ["watches"],
        "company_types": ["dealer"],
        "role_terms": ["owner OR founder"],
        "combinations_per_run": 2,
    }

    assert [item["role"] for item in plan_combinations(plan)] == ["owner", "founder"]


def test_configured_plans_are_created_once_and_use_shared_defaults():
    class Repo:
        def __init__(self):
            self.created = []

        def list_acquisition_plans(self, limit):
            return [{"name": "Existing"}]

        def create_acquisition_plan(self, **values):
            self.created.append(values)

    config = SimpleNamespace(raw={"acquisition": {
        "regions": ["UAE", "India"],
        "role_terms": ["owner", "CEO"],
        "pool_type": "public",
        "daily_lead_limit": 8,
        "combinations_per_run": 8,
        "plans": [
            {"name": "Existing", "industries": ["watches"], "company_types": ["retailer"]},
            {"name": "New", "industries": ["luxury cars"], "company_types": ["dealer"]},
        ],
    }})
    repo = Repo()

    summary = AcquisitionPlannerService(config, repo).sync_configured_plans()

    assert summary == {"configured": 2, "created": 1, "existing": 1}
    assert repo.created == [{
        "name": "New",
        "regions": ["UAE", "India"],
        "industries": ["luxury cars"],
        "company_types": ["dealer"],
        "role_terms": ["owner", "CEO"],
        "owner_user_id": None,
        "pool_type": "public",
        "daily_lead_limit": 8,
        "combinations_per_run": 8,
    }]


def test_due_plan_respects_actual_usage_and_never_sends_email():
    class Repo:
        def list_due_acquisition_plans(self, limit):
            return [{
                "id": 4,
                "regions": ["India"],
                "industries": ["watches"],
                "company_types": ["dealer"],
                "role_terms": ["owner"],
                "owner_user_id": 8,
                "daily_lead_limit": 5,
                "combinations_per_run": 1,
            }]

        def begin_acquisition_plan_run(self, plan_id, combinations):
            return {"id": 9}

        def get_active_user(self, user_id):
            return {"id": user_id, "role": "sales", "username": "ivan"}

        def finish_acquisition_plan_run(self, run_id, **kwargs):
            self.finished = kwargs

    class Quota:
        consumed = []

        def __init__(self, config, repo):
            pass

        def snapshot(self, user):
            return {"source": {"remaining_user": 4, "remaining_global": 20}}

        def consume(self, user, kind, amount):
            self.consumed.append((user["id"], kind, amount))

    class Search:
        def __init__(self, config, repo):
            pass

        def run(self, criteria, limit, user, pool_type):
            assert limit == 4
            assert user["id"] == 8
            assert pool_type == "private"
            return {"results": 3, "promoted": 2}

    repo = Repo()
    result = AcquisitionPlannerService(
        SimpleNamespace(), repo, search_factory=Search, quota_factory=Quota
    ).run_due()

    assert result == {"plans": 1, "completed": 1, "failed": 0, "results": 3, "promoted": 2}
    assert Quota.consumed == [(8, "source", 3)]
    assert repo.finished["status"] == "completed"


def test_due_plan_only_advances_combinations_that_ran():
    class Repo:
        def list_due_acquisition_plans(self, limit):
            return [{
                "id": 4,
                "regions": ["India", "United Kingdom", "Russia"],
                "industries": ["watches"],
                "company_types": ["dealer"],
                "role_terms": ["owner"],
                "owner_user_id": 8,
                "daily_lead_limit": 3,
                "combinations_per_run": 3,
            }]

        def begin_acquisition_plan_run(self, plan_id, combinations):
            return {"id": 9}

        def get_active_user(self, user_id):
            return {"id": user_id, "role": "sales"}

        def finish_acquisition_plan_run(self, run_id, **kwargs):
            self.finished = kwargs

    class Quota:
        consumed = []

        def __init__(self, config, repo):
            pass

        def snapshot(self, user):
            return {"source": {"remaining_user": 2, "remaining_global": 2}}

        def consume(self, user, kind, amount):
            pass

    class Search:
        def __init__(self, config, repo):
            pass

        def run(self, criteria, limit, user, pool_type):
            assert limit == 1
            assert user["id"] == 8
            assert pool_type == "private"
            return {"results": 1, "promoted": 0}

    repo = Repo()
    AcquisitionPlannerService(SimpleNamespace(), repo, search_factory=Search, quota_factory=Quota).run_due()

    assert repo.finished["cursor_advance"] == 2
    assert len(repo.finished["metrics"]["combinations"]) == 2


def test_public_plan_uses_global_quota_without_owner_or_private_pool():
    class Repo:
        def list_due_acquisition_plans(self, limit):
            return [{
                "id": 4,
                "pool_type": "public",
                "regions": ["India"],
                "industries": ["watches"],
                "company_types": ["dealer"],
                "role_terms": ["owner"],
                "owner_user_id": None,
                "daily_lead_limit": 5,
                "combinations_per_run": 1,
            }]

        def begin_acquisition_plan_run(self, plan_id, combinations):
            return {"id": 9}

        def get_active_user(self, user_id):
            raise AssertionError("public plans must not load an owner")

        def finish_acquisition_plan_run(self, run_id, **kwargs):
            self.finished = kwargs

    class Quota:
        consumed = []

        def __init__(self, config, repo):
            pass

        def remaining_global(self, kind):
            assert kind == "source"
            return 4

        def snapshot(self, user):
            raise AssertionError("public plans must not read a user quota")

        def consume(self, user, kind, amount):
            raise AssertionError("public plans must not consume a user quota")

        def consume_global(self, kind, amount):
            self.consumed.append((kind, amount))

    class Search:
        def __init__(self, config, repo):
            pass

        def run(self, criteria, limit, user, pool_type):
            assert user is None
            assert pool_type == "public"
            return {"results": 3, "promoted": 2}

    repo = Repo()
    result = AcquisitionPlannerService(SimpleNamespace(), repo, search_factory=Search, quota_factory=Quota).run_due()

    assert result == {"plans": 1, "completed": 1, "failed": 0, "results": 3, "promoted": 2}
    assert repo.finished["status"] == "completed"
    assert Quota.consumed == [("source", 3)]


def test_private_plan_retry_keeps_owner_scope_and_does_not_charge_failed_run():
    class Repo:
        def __init__(self):
            self.finishes = []
            self.run_id = 0

        def list_due_acquisition_plans(self, limit):
            return [{
                "id": 4,
                "pool_type": "private",
                "regions": ["India"],
                "industries": ["watches"],
                "company_types": ["dealer"],
                "role_terms": ["owner"],
                "owner_user_id": 8,
                "daily_lead_limit": 5,
                "combinations_per_run": 1,
            }]

        def begin_acquisition_plan_run(self, plan_id, combinations):
            self.run_id += 1
            return {"id": self.run_id}

        def get_active_user(self, user_id):
            return {"id": user_id, "role": "sales"}

        def finish_acquisition_plan_run(self, run_id, **kwargs):
            self.finishes.append(kwargs)

    class Quota:
        consumed = []

        def __init__(self, config, repo):
            pass

        def snapshot(self, user):
            return {"source": {"remaining_user": 4, "remaining_global": 20}}

        def consume(self, user, kind, amount):
            self.consumed.append((user["id"], kind, amount))

    class Search:
        calls = []

        def __init__(self, config, repo):
            pass

        def run(self, criteria, limit, user, pool_type):
            self.calls.append((user["id"], pool_type))
            if len(self.calls) == 1:
                raise RuntimeError("temporary search failure")
            return {"results": 2, "promoted": 1}

    repo = Repo()
    service = AcquisitionPlannerService(SimpleNamespace(), repo, search_factory=Search, quota_factory=Quota)
    assert service.run_due()["failed"] == 1
    assert service.run_due()["completed"] == 1

    assert [item["status"] for item in repo.finishes] == ["failed", "completed"]
    assert repo.finishes[0]["cursor_advance"] == 0
    assert Search.calls == [(8, "private"), (8, "private")]
    assert Quota.consumed == [(8, "source", 2)]


def test_united_kingdom_uses_local_search_profile():
    profile = detect_regional_profile("United Kingdom", "luxury watches")

    assert profile.key == "europe"
    assert profile.country == "GB"
    assert "head of buying" in profile.role_terms
