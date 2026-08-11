from types import SimpleNamespace

from sales_automation.llm_gateway import LLMGateway


class Repo:
    def __init__(self, *, allow_budget=True):
        self.allow_budget = allow_budget
        self.cache = {}
        self.reservations = []

    def get_llm_gateway_cache(self, key, owner_user_id):
        return self.cache.get((owner_user_id, key))

    def store_llm_gateway_cache(self, key, owner_user_id, provider, model, operation, response, ttl_seconds):
        self.cache[(owner_user_id, key)] = response

    def reserve_llm_gateway_budget(self, *args):
        self.reservations.append(args)
        return self.allow_budget


class Http:
    def __init__(self, response=None):
        self.response = response or {"choices": [{"message": {"content": "generated"}}]}
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


def config(*, mode="high_value", provider="deepseek", api_key="key"):
    return SimpleNamespace(
        raw={
            "llm": {
                "provider": provider,
                "model": "test-model",
                "gateway": {
                    "mode": mode,
                    "icp_threshold": 70,
                    "daily_calls": 2,
                    "daily_input_chars": 1000,
                    "daily_output_chars": 1000,
                },
            }
        },
        apis={f"{provider}_key": api_key} if api_key else {},
    )


def complete(gateway, *, score=80):
    return gateway.complete(
        operation="draft",
        messages=[{"role": "user", "content": "private prompt"}],
        contact={"lead_score": score, "owner_user_id": 7},
        max_tokens=20,
        temperature=0.2,
        validate=lambda value: value,
    )


def test_high_value_policy_denies_low_score_and_off_mode_even_with_cache():
    repo = Repo()
    http = Http()
    all_gateway = LLMGateway(config(mode="all"), repo, http=http)
    assert complete(all_gateway) == "generated"

    assert complete(LLMGateway(config(mode="high_value"), repo, http=http), score=69) is None
    assert complete(LLMGateway(config(mode="off"), repo, http=http)) is None
    assert len(http.calls) == 1


def test_success_is_cached_without_second_budget_or_http_call():
    repo = Repo()
    http = Http()
    gateway = LLMGateway(config(), repo, http=http)

    assert complete(gateway) == "generated"
    assert complete(gateway) == "generated"
    assert len(repo.reservations) == 1
    assert len(http.calls) == 1
    assert list(repo.cache.values()) == ["generated"]
    assert "private prompt" not in next(iter(repo.cache))[1]


def test_missing_key_or_exhausted_budget_returns_fallback_signal_without_call():
    missing_key_repo = Repo()
    missing_key_http = Http()
    assert complete(LLMGateway(config(api_key=""), missing_key_repo, http=missing_key_http)) is None
    assert missing_key_repo.reservations == []
    assert missing_key_http.calls == []
    assert not LLMGateway(config(api_key=""), missing_key_repo, http=missing_key_http).can_generate({"lead_score": 90})

    denied_repo = Repo(allow_budget=False)
    denied_http = Http()
    assert complete(LLMGateway(config(), denied_repo, http=denied_http)) is None
    assert len(denied_repo.reservations) == 1
    assert denied_http.calls == []


def test_openai_uses_responses_api_default_base_url():
    repo = Repo()
    http = Http({"output_text": "openai result"})
    gateway = LLMGateway(config(mode="all", provider="openai"), repo, http=http)

    assert complete(gateway) == "openai result"
    assert http.calls[0][0][1] == "https://api.openai.com/v1/responses"


def test_cache_is_scoped_by_owner_and_invalid_output_is_not_cached():
    repo = Repo()
    http = Http()
    gateway = LLMGateway(config(mode="all"), repo, http=http)
    args = {
        "operation": "draft",
        "messages": [{"role": "user", "content": "same prompt"}],
        "max_tokens": 20,
        "temperature": 0.2,
        "validate": lambda value: value if value == "generated" else None,
    }

    assert gateway.complete(contact={"owner_user_id": 1}, **args) == "generated"
    assert gateway.complete(contact={"owner_user_id": 2}, **args) == "generated"
    assert len(http.calls) == 2
    assert len(repo.cache) == 2

    invalid_repo = Repo()
    invalid_http = Http({"choices": [{"message": {"content": "bad"}}]})
    assert LLMGateway(config(mode="all"), invalid_repo, http=invalid_http).complete(
        contact={"owner_user_id": 1},
        **{**args, "validate": lambda _value: None},
    ) is None
    assert invalid_repo.cache == {}
