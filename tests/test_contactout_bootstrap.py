import json

from contactout_bridge.bootstrap_sessions import load_credentials, session_path


def test_loads_alternating_credentials_without_cli_secrets(tmp_path):
    source = tmp_path / "accounts.env"
    source.write_text("one@example.com\nsecret-1\n\ntwo@example.com\nsecret-2\n", encoding="utf-8")

    accounts = load_credentials(source)

    assert accounts["contactout-account-01"]["email"] == "one@example.com"
    assert accounts["contactout-account-02"]["password"] == "secret-2"


def test_loads_labeled_credentials(tmp_path):
    source = tmp_path / "accounts.env"
    source.write_text("邮箱: one@example.com\n密码：secret-1\n", encoding="utf-8")

    accounts = load_credentials(source)

    assert accounts["contactout-account-01"] == {"email": "one@example.com", "password": "secret-1"}


def test_loads_json_credentials(tmp_path):
    source = tmp_path / "accounts.json"
    source.write_text(
        json.dumps({"accounts": {"contactout-account-08": {"email": "a@example.com", "password": "secret"}}}),
        encoding="utf-8",
    )

    accounts = load_credentials(source)

    assert list(accounts) == ["contactout-account-08"]


def test_session_filename_matches_account_pool(tmp_path):
    path = session_path(tmp_path, "contactout-account-01")

    assert path.name == "3f413823f543837f66e0e3d7d4d1a7755314209a2594de067a897854e35818b7.json"
