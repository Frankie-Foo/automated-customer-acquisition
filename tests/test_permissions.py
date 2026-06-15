from contextlib import contextmanager

from sales_automation.db import Repository


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if "COUNT(*) FILTER" in query and "FROM contacts c" in query:
            return FakeCursor([{"new_contacts_today": 0, "valid_emails_today": 0, "queued": 0, "bounced": 0, "replied": 0}])
        if "FROM email_events e" in query:
            return FakeCursor(
                [
                    {
                        "sent_today": 0,
                        "opened_today": 0,
                        "clicked_today": 0,
                        "replied_events_today": 0,
                        "bounced_events_today": 0,
                        "opened_no_reply": 0,
                    }
                ]
            )
        if "FROM sales_users u" in query:
            return FakeCursor(
                [
                    {
                        "id": 2,
                        "username": "April",
                        "display_name": "April",
                        "role": "sales",
                        "active": True,
                        "daily_source_limit": 100,
                        "daily_send_limit": 100,
                        "source_count_today": 3,
                        "send_count_today": 4,
                        "owned_contacts": 9,
                    }
                ]
            )
        if "email_provider_stats" in query:
            raise AssertionError("sales users must not query provider stats")
        if "GROUP BY reason" in query:
            return FakeCursor([])
        return FakeCursor([])


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeDb:
    def __init__(self):
        self.conn = FakeConn()

    @contextmanager
    def connect(self):
        yield self.conn


def test_sales_operations_report_is_scoped_to_current_user():
    db = FakeDb()
    report = Repository(db).operations_report(user={"id": 2, "role": "sales"})

    assert report["scope"] == "self"
    assert [row["username"] for row in report["by_user"]] == ["April"]
    assert report["provider_stats"] == []
    user_queries = [call for call in db.conn.calls if "FROM sales_users u" in call[0]]
    assert user_queries
    assert "WHERE u.id = %s" in user_queries[0][0]
    assert user_queries[0][1] == (2,)

