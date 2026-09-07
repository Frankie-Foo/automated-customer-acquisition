"""Optional isolated PostgreSQL contract test; creates only temporary tables."""
import ast
import inspect
import os
import textwrap
from contextlib import contextmanager

import psycopg
import pytest
from psycopg.rows import dict_row

from sales_automation.db import Repository


@pytest.mark.skipif(not os.getenv("SALESBOT_QA_PG_DSN"), reason="isolated QA PostgreSQL not configured")
def test_experiment_window_attribution_and_owner_scope():
    tree = ast.parse(textwrap.dedent(inspect.getsource(Repository.outbound_quality_dashboard)))
    query = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                 and isinstance(n.value, str) and "SELECT e.*" in n.value)
    with psycopg.connect(os.environ["SALESBOT_QA_PG_DSN"], row_factory=dict_row) as conn:
        conn.execute("CREATE TEMP TABLE outbound_experiments (id bigint PRIMARY KEY, owner_user_id bigint, created_at timestamptz)")
        conn.execute("CREATE TEMP TABLE email_drafts (experiment_id bigint, experiment_variant text)")
        conn.execute("""CREATE TEMP TABLE outreach_messages (id bigint PRIMARY KEY, contact_id bigint,
            experiment_id bigint, experiment_variant text, provider_message_id text,
            sent_at timestamptz, delivered_at timestamptz, opened_at timestamptz, bounced_at timestamptz)""")
        conn.execute("""CREATE TEMP TABLE interactions (id bigint PRIMARY KEY, contact_id bigint,
            interaction_type text, metadata jsonb, occurred_at timestamptz)""")
        conn.execute("INSERT INTO outbound_experiments VALUES (1, 2, NOW()), (2, 3, NOW())")
        conn.execute("INSERT INTO email_drafts VALUES (1, 'A'), (1, 'B'), (2, 'A')")
        conn.execute("""INSERT INTO outreach_messages (id, contact_id, experiment_id, experiment_variant, provider_message_id, sent_at)
            VALUES (1, 10, 1, 'A', 'a', NOW() - INTERVAL '20 days'),
                   (2, 10, 1, 'B', 'b', NOW() - INTERVAL '20 days'),
                   (3, 11, 1, 'B', 'new', NOW() - INTERVAL '1 day')""")
        conn.execute("""INSERT INTO interactions VALUES
            (1, 10, 'email_reply', '{"outbound_message_id":"a","reply_classification":{"positive":true}}', NOW() - INTERVAL '19 days'),
            (2, 10, 'email_reply', '{"outbound_message_id":"a","reply_classification":{"positive":true}}', NOW() - INTERVAL '18 days'),
            (3, 10, 'email_reply', '{"outbound_message_id":"b","reply_classification":{"positive":true}}', NOW()),
            (4, 10, 'email_reply', '{"reply_classification":{"positive":true}}', NOW() - INTERVAL '19 days'),
            (5, 10, 'email_reply', '{"outbound_message_id":"b","reply_classification":{"label":"unsubscribe"}}', NOW() - INTERVAL '19 days'),
            (6, 11, 'email_reply', '{"outbound_message_id":"new","reply_classification":{"positive":true}}', NOW())""")
        rows = conn.execute(query, (2, 2)).fetchall()
        assert len(rows) == 1
        variants = {v['name']: v for v in rows[0]['measured_variants']}
        assert variants['A']['sent'] == variants['B']['sent'] == 1
        assert variants['A']['positive_replies'] == variants['A']['replies'] == 1
        assert variants['B']['positive_replies'] == 0
        assert variants['B']['unsubscribed'] == 1
        assert conn.execute(query, (999, 999)).fetchall() == []
        assert len(conn.execute(query, (None, None)).fetchall()) == 2


@pytest.mark.skipif(not os.getenv("SALESBOT_QA_PG_DSN"), reason="isolated QA PostgreSQL not configured")
def test_email_performance_real_query_has_no_list_cap_and_keeps_scope_and_windows():
    with psycopg.connect(os.environ["SALESBOT_QA_PG_DSN"], row_factory=dict_row) as conn:
        class Db:
            @contextmanager
            def connect(self):
                yield conn

        repo = Repository(Db())
        conn.execute("CREATE TEMP TABLE contacts (id bigint PRIMARY KEY, owner_user_id bigint)")
        conn.execute("""CREATE TEMP TABLE outreach_messages (id bigint, contact_id bigint, sent_at timestamptz,
            channel text DEFAULT 'email', metadata jsonb DEFAULT '{}', provider_message_id text, bounced_at timestamptz)""")
        conn.execute("""CREATE TEMP TABLE interactions (contact_id bigint, interaction_type text,
            metadata jsonb, occurred_at timestamptz)""")
        conn.execute("INSERT INTO contacts VALUES (10, 2), (20, 3)")
        conn.execute("""INSERT INTO outreach_messages(id,contact_id,sent_at,provider_message_id) VALUES
            (1,10,NOW()-INTERVAL '20 days','a'), (2,10,NOW()-INTERVAL '2 days','new'),
            (3,20,NOW()-INTERVAL '20 days','other'), (4,10,NOW()-INTERVAL '20 days','dry'),
            (5,10,NOW()-INTERVAL '20 days','phone'), (6,10,NOW()-INTERVAL '91 days','old'),
            (7,10,NOW()+INTERVAL '1 day','future')""")
        conn.execute("UPDATE outreach_messages SET metadata = '{\"dry_run\":true}' WHERE id=4")
        conn.execute("UPDATE outreach_messages SET channel='whatsapp' WHERE id=5")
        conn.execute("""INSERT INTO outreach_messages(id,contact_id,sent_at,provider_message_id)
            SELECT id,10,NOW()-INTERVAL '20 days','bulk-'||id FROM generate_series(100,260) id""")
        conn.execute("""INSERT INTO interactions VALUES
            (10,'email_reply','{"outbound_message_id":"a","reply_classification":{"positive":true,"label":"positive_soft"}}',NOW()-INTERVAL '10 days'),
            (10,'email_reply','{"outbound_message_id":"a","reply_classification":{"positive":true,"label":"positive_soft"}}',NOW()-INTERVAL '9 days'),
            (10,'email_reply','{"reply_classification":{"positive":true}}',NOW()-INTERVAL '19 days'),
            (10,'email_reply','{"outbound_message_id":"bulk-100","reply_classification":{"label":"ooo"}}',NOW()-INTERVAL '19 days')""")

        def totals(user, days):
            result = repo.email_performance(user=user, observation_days=days)
            return {key: sum(row[key] for row in result['weeks']) for key in ('sent','matured','replied','positive')}

        sales = {'id': 2, 'role': 'sales'}
        assert totals(sales,14) == dict(sent=163,matured=162,replied=1,positive=1)
        assert totals(sales,7) == dict(sent=163,matured=162,replied=0,positive=0)
        assert totals(sales,30) == dict(sent=163,matured=0,replied=0,positive=0)
        assert totals({'id': 3,'role': 'sales'},14)['sent'] == 1
        assert totals({'id': 1,'role': 'admin'},14)['sent'] == 164
        assert repo.email_performance(user={'id': 99,'role': 'sales'})['weeks'] == []


def test_email_performance_requires_identity_and_valid_window():
    repo = Repository(None)
    with pytest.raises(PermissionError):
        repo.email_performance(user=None)
    for days in (0, 8, 365):
        with pytest.raises(ValueError):
            repo.email_performance(user={'id': 2, 'role': 'sales'}, observation_days=days)
