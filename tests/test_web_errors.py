from sales_automation.web import _api_error_status


class _ApolloDispatchConflict(Exception):
    sqlstate = "55000"


def test_apollo_dispatch_database_fence_returns_conflict():
    error = _ApolloDispatchConflict("apollo dispatch is active; contact cannot be reassigned or deleted")

    assert _api_error_status(error) == 409


def test_unrelated_database_prerequisite_error_remains_internal_error():
    error = _ApolloDispatchConflict("unrelated prerequisite state")

    assert _api_error_status(error) == 500
