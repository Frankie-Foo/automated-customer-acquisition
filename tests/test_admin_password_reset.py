from contextlib import contextmanager

from sales_automation.db import Repository


class _Connection:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((sql, params))
        return self

    def fetchone(self):
        return {
            "id": 7,
            "username": "sales07",
            "display_name": "Sales 07",
            "role": "sales",
            "daily_source_limit": 100,
            "daily_send_limit": 200,
            "reply_to_email": None,
            "active": True,
            "must_change_password": True,
            "created_at": None,
        }


class _Database:
    def __init__(self):
        self.connection = _Connection()

    @contextmanager
    def connect(self):
        yield self.connection


def test_admin_password_reset_revokes_existing_sessions():
    database = _Database()
    user = Repository(database).reset_user_password(7, "strong-temporary-password")

    assert user["must_change_password"] is True
    assert any("DELETE FROM user_sessions" in sql for sql, _ in database.connection.queries)
