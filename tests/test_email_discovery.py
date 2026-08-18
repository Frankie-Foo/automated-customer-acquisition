import json
import threading
from types import SimpleNamespace

from sales_automation.email_discovery import EmailCandidate, EmailDiscoveryEngine, GitHubCommitsEmailProvider, GravatarEmailProvider, SmtpVerifyProvider, _provider_lookup_key


class PublicOnlyProvider:
    def discover(self, contact, domain):
        return [EmailCandidate.build("sales@example.com", "public_website", "unverified", 30, "company_generic")]


class PersonalProvider:
    def discover(self, contact, domain):
        return [EmailCandidate.build("ada@example.com", "prospeo", "valid", 95, "personal_work")]


class BrokenProvider:
    name = "broken"

    def discover(self, contact, domain):
        raise RuntimeError("quota exhausted")


class PaidPersonalProvider(PersonalProvider):
    name = "prospeo"


class PaidFallbackProvider(PublicOnlyProvider):
    name = "hunter"
    called = 0

    def discover(self, contact, domain):
        type(self).called += 1
        return super().discover(contact, domain)


class CountingPaidProvider(PersonalProvider):
    name = "prospeo"

    def __init__(self):
        self.calls = 0

    def discover(self, contact, domain):
        self.calls += 1
        return super().discover(contact, domain)


class RateLimitedProvider:
    name = "prospeo"

    def discover(self, contact, domain):
        raise RuntimeError("HTTP 429: retry later")


def test_public_company_email_is_candidate_not_selected():
    result = EmailDiscoveryEngine([PublicOnlyProvider()]).discover({}, "example.com")

    assert result["email_status"] == "unknown"
    assert "email" not in result
    assert result["email_candidates"][0]["category"] == "company_generic"


def test_valid_personal_email_is_selected():
    result = EmailDiscoveryEngine([PublicOnlyProvider(), PersonalProvider()]).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert result["email_status"] == "valid"
    assert result["email_source"] == "prospeo"


def test_waterfall_continues_collecting_candidates_after_valid():
    result = EmailDiscoveryEngine([PersonalProvider(), PublicOnlyProvider()]).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert {item["source"] for item in result["email_candidates"]} == {"prospeo", "public_website"}


def test_waterfall_continues_after_provider_error():
    stats = []

    result = EmailDiscoveryEngine(
        [BrokenProvider(), PersonalProvider()],
        stats_recorder=lambda provider, **fields: stats.append((provider, fields)),
    ).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert any(provider == "broken" and fields["errors"] == 1 for provider, fields in stats)


def test_provider_stats_recorder_is_called():
    stats = []

    result = EmailDiscoveryEngine([PersonalProvider()], stats_recorder=lambda provider, **fields: stats.append((provider, fields))).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert stats[0][0] == "PersonalProvider"
    assert stats[0][1]["calls"] == 1
    assert stats[0][1]["candidates"] == 1
    assert stats[0][1]["valid_candidates"] == 1
    assert stats[0][1]["selected"] == 1


def test_paid_provider_over_contact_budget_is_skipped():
    PaidFallbackProvider.called = 0
    stats = []

    result = EmailDiscoveryEngine(
        [PaidPersonalProvider(), PaidFallbackProvider()],
        stats_recorder=lambda provider, **fields: stats.append((provider, fields)),
    ).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert PaidFallbackProvider.called == 0
    assert ("hunter", {"calls": 0, "skipped": 1, "credits_used": 0}) in stats


def test_three_credit_budget_reaches_hunter_after_prospeo_miss():
    class ProspeoMiss:
        name = "prospeo"

        def discover(self, contact, domain):
            return []

    class HunterHit:
        name = "hunter"

        def discover(self, contact, domain):
            return [EmailCandidate.build("ada@example.com", self.name, "valid", 90, "personal_work")]

    result = EmailDiscoveryEngine([ProspeoMiss(), HunterHit()], max_paid_credits=3).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert result["email_source"] == "hunter"


def test_eligible_providers_run_in_parallel_and_selection_remains_ordered():
    barrier = threading.Barrier(2)

    class ProspeoHit:
        name = "prospeo"

        def discover(self, contact, domain):
            barrier.wait(timeout=1)
            return [EmailCandidate.build("first@example.com", self.name, "valid", 90, "personal_work")]

    class NinjaPearHit:
        name = "ninjapear"

        def discover(self, contact, domain):
            barrier.wait(timeout=1)
            return [EmailCandidate.build("second@example.com", self.name, "valid", 95, "personal_work")]

    result = EmailDiscoveryEngine([ProspeoHit(), NinjaPearHit()], max_paid_credits=2).discover({}, "example.com")

    assert result["email"] == "first@example.com"
    assert {item["email"] for item in result["email_candidates"]} == {"first@example.com", "second@example.com"}


def test_existing_valid_email_avoids_paid_provider_calls():
    provider = CountingPaidProvider()

    result = EmailDiscoveryEngine([type("Existing", (), {"name": "existing", "discover": lambda self, contact, domain: [EmailCandidate.build("known@example.com", "existing", "valid", 100, "personal_work")]})(), provider]).discover({}, "example.com")

    assert result["email"] == "known@example.com"
    assert provider.calls == 0


def test_paid_provider_cache_hit_skips_api_and_budget():
    provider = CountingPaidProvider()
    reserved = []
    cached = [EmailCandidate.build("cached@example.com", "prospeo", "valid", 95, "personal_work").__dict__]

    result = EmailDiscoveryEngine(
        [provider],
        daily_credit_limits={"prospeo": 10},
        budget_reserver=lambda *args: reserved.append(args) or True,
        cache_reader=lambda *args: cached,
    ).discover({"first_name": "Ada", "last_name": "Lovelace"}, "example.com")

    assert result["email"] == "cached@example.com"
    assert provider.calls == 0
    assert reserved == []


def test_paid_provider_budget_exhaustion_is_reported_without_calling_api():
    provider = CountingPaidProvider()

    result = EmailDiscoveryEngine(
        [provider],
        daily_credit_limits={"prospeo": 0},
        budget_reserver=lambda provider, amount, limit: False,
    ).discover({"first_name": "Ada", "last_name": "Lovelace"}, "example.com")

    assert provider.calls == 0
    assert result["email_status"] == "unknown"
    assert result["_provider_warnings"] == ["prospeo:provider_daily_budget_exhausted"]


def test_paid_provider_stores_negative_cache():
    provider = PaidFallbackProvider()
    writes = []

    EmailDiscoveryEngine(
        [provider],
        daily_credit_limits={"hunter": 10},
        budget_reserver=lambda *args: True,
        cache_writer=lambda *args: writes.append(args),
    ).discover({"first_name": "Ada", "last_name": "Lovelace"}, "example.com")

    assert writes[0][0:2] == ("hunter", "email_discovery")
    assert writes[0][3] == "no_match"
    assert writes[0][5] == 24 * 3600


def test_paid_provider_429_is_reported_and_not_cached():
    writes = []
    stats = []

    result = EmailDiscoveryEngine(
        [RateLimitedProvider()],
        daily_credit_limits={"prospeo": 10},
        budget_reserver=lambda *args: True,
        cache_writer=lambda *args: writes.append(args),
        stats_recorder=lambda provider, **fields: stats.append((provider, fields)),
    ).discover({"first_name": "Ada", "last_name": "Lovelace"}, "example.com")

    assert result["_provider_warnings"] == ["prospeo:provider_rate_limited"]
    assert writes == []
    assert stats == [("prospeo", {"calls": 1, "errors": 1, "last_error": "HTTP 429: retry later", "credits_used": 0})]


def test_paid_cache_key_separates_provider_person_ids():
    first = _provider_lookup_key({"source_person_id": "person-a", "first_name": "Ada", "last_name": "Lovelace"}, "example.com")
    second = _provider_lookup_key({"source_person_id": "person-b", "first_name": "Ada", "last_name": "Lovelace"}, "example.com")

    assert first != second


def test_dedupe_retains_verified_candidate_over_higher_confidence_unverified_duplicate():
    verified = EmailCandidate.build("ada@example.com", "hunter", "valid", 70, "personal_work")
    unverified = EmailCandidate.build("ada@example.com", "public", "unverified", 95, "personal_work")

    result = EmailDiscoveryEngine([
        type("Verified", (), {"discover": lambda self, contact, domain: [verified]})(),
        type("Unverified", (), {"discover": lambda self, contact, domain: [unverified]})(),
    ]).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert result["email_candidates"][0]["status"] == "valid"


def test_github_provider_extracts_company_commit_email(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit=-1):
            return json.dumps([
                {"payload": {"commits": [{"author": {"email": "ada@example.com"}}]}},
                {"payload": {"commits": [{"author": {"email": "123+ada@users.noreply.github.com"}}]}},
            ]).encode()

    monkeypatch.setattr("sales_automation.email_discovery.safe_urlopen", lambda request, timeout=8: Response())

    result = GitHubCommitsEmailProvider().discover({"social_profiles": {"github": "https://github.com/ada"}}, "example.com")

    assert len(result) == 1
    assert result[0].email == "ada@example.com"
    assert result[0].status == "valid"


def test_gravatar_provider_is_candidate_only(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("sales_automation.email_discovery.safe_urlopen", lambda request, timeout=5: Response())

    result = EmailDiscoveryEngine([GravatarEmailProvider()]).discover({"first_name": "Ada", "last_name": "Lovelace"}, "example.com")

    assert "email" not in result
    assert result["email_candidates"][0]["source"] == "gravatar"
    assert result["email_candidates"][0]["status"] == "unverified"


def test_smtp_verify_provider_classifies_250_and_550(monkeypatch):
    class SMTP:
        def __init__(self, domain, timeout=10):
            self.domain = domain

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def ehlo_or_helo_if_needed(self):
            return None

        def mail(self, sender):
            return 250, b"ok"

        def rcpt(self, email):
            if email.startswith("ada."):
                return 550, b"no"
            return 250, b"ok"

    monkeypatch.setattr("smtplib.SMTP", SMTP)

    result = SmtpVerifyProvider().discover({"first_name": "Ada", "last_name": "Lovelace"}, "example.com")

    assert result[0].status == "risky"
    assert result[1].status == "valid"
