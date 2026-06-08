import json
from types import SimpleNamespace

from sales_automation.email_discovery import EmailCandidate, EmailDiscoveryEngine, GitHubCommitsEmailProvider, GravatarEmailProvider, SmtpVerifyProvider


class PublicOnlyProvider:
    def discover(self, contact, domain):
        return [EmailCandidate.build("sales@example.com", "public_website", "unverified", 30, "company_generic")]


class PersonalProvider:
    def discover(self, contact, domain):
        return [EmailCandidate.build("ada@example.com", "prospeo", "valid", 95, "personal_work")]


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


def test_provider_stats_recorder_is_called():
    stats = []

    result = EmailDiscoveryEngine([PersonalProvider()], stats_recorder=lambda provider, **fields: stats.append((provider, fields))).discover({}, "example.com")

    assert result["email"] == "ada@example.com"
    assert stats[0][0] == "PersonalProvider"
    assert stats[0][1]["calls"] == 1
    assert stats[0][1]["candidates"] == 1
    assert stats[0][1]["valid_candidates"] == 1
    assert stats[0][1]["selected"] == 1


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

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=8: Response())

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

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=5: Response())

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
