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
        {"location": "United Kingdom", "industry": "luxury cars", "company_keyword": "distributor", "role": "owner"},
        {"location": "India", "industry": "watches", "company_keyword": "distributor", "role": "owner"},
    ]


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

        def run(self, criteria, limit, user):
            assert limit == 4
            return {"results": 3, "promoted": 2}

    repo = Repo()
    result = AcquisitionPlannerService(
        SimpleNamespace(), repo, search_factory=Search, quota_factory=Quota
    ).run_due()

    assert result == {"plans": 1, "completed": 1, "failed": 0, "results": 3, "promoted": 2}
    assert Quota.consumed == [(8, "source", 3)]
    assert repo.finished["status"] == "completed"


def test_united_kingdom_uses_local_search_profile():
    profile = detect_regional_profile("United Kingdom", "luxury watches")

    assert profile.key == "europe"
    assert profile.country == "GB"
    assert "head of buying" in profile.role_terms
