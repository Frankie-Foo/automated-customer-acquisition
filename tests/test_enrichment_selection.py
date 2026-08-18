from sales_automation.db import Repository


class _Rows:
    def fetchall(self):
        return []


class _Connection:
    def __init__(self):
        self.query = ""

    def execute(self, query, _params):
        self.query = query
        return _Rows()


class _Context:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return None


class _Db:
    def __init__(self):
        self.connection = _Connection()

    def connect(self):
        return _Context(self.connection)


def test_failed_enrichment_waits_before_retrying_paid_providers():
    db = _Db()

    Repository(db).list_for_enrichment(25)

    assert "status = 'new' AND (enriched_at IS NULL OR enriched_at < NOW() - INTERVAL '24 hours')" in db.connection.query
