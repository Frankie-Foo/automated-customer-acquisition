from sales_automation.auth import hash_password, public_user, verify_password


def test_public_user_exposes_password_change_flag():
    user = {
        "id": 1,
        "username": "sales01",
        "display_name": "Sales 01",
        "role": "sales",
        "daily_source_limit": 100,
        "daily_send_limit": 100,
        "must_change_password": True,
    }

    assert public_user(user)["must_change_password"] is True


def test_password_hash_roundtrip():
    stored = hash_password("temporary-password")

    assert verify_password("temporary-password", stored) is True
    assert verify_password("wrong-password", stored) is False
