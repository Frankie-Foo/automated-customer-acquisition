from __future__ import annotations

import csv
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import AppConfig
from .status import validate_status


def _psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError as exc:
        raise RuntimeError("Install dependencies first: pip install -e .") from exc
    return psycopg, dict_row


class Database:
    def __init__(self, config: AppConfig):
        self.config = config

    @contextmanager
    def connect(self):
        psycopg, dict_row = _psycopg()
        db = self.config.database
        conn = psycopg.connect(
            host=db["host"],
            port=int(db.get("port", 5432)),
            user=db["user"],
            password=db["password"],
            dbname=db["dbname"],
            connect_timeout=int(db.get("connect_timeout") or 10),
            row_factory=dict_row,
        )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self, migration_dir: Path = Path("migrations")) -> list[str]:
        applied: list[str] = []
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
            existing = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for path in sorted(migration_dir.glob("*.sql")):
                if path.name in existing:
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.name,))
                applied.append(path.name)
        return applied


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def upsert_contacts(self, contacts: Iterable[dict[str, Any]]) -> tuple[int, int]:
        inserted = skipped = 0
        sql = """
        INSERT INTO contacts (
          linkedin_url, first_name, last_name, email, email_status, job_title, company_name,
          company_domain, industry, location, company_size, status, source_person_id, source
        ) VALUES (
          %(linkedin_url)s, %(first_name)s, %(last_name)s, %(email)s, %(email_status)s, %(job_title)s, %(company_name)s,
          %(company_domain)s, %(industry)s, %(location)s, %(company_size)s, %(status)s, %(source_person_id)s, %(source)s
        )
        ON CONFLICT (linkedin_url) DO UPDATE
        SET source_person_id = COALESCE(EXCLUDED.source_person_id, contacts.source_person_id),
            email = CASE
                WHEN EXCLUDED.email IS NOT NULL AND EXCLUDED.email NOT LIKE '%%*%%' THEN EXCLUDED.email
                ELSE contacts.email
            END,
            email_status = CASE
                WHEN EXCLUDED.email IS NOT NULL AND EXCLUDED.email NOT LIKE '%%*%%' THEN EXCLUDED.email_status
                ELSE contacts.email_status
            END,
            status = CASE
                WHEN EXCLUDED.email_status = 'valid' THEN 'enriched'::contact_status
                ELSE contacts.status
            END,
            enrich_error = CASE
                WHEN EXCLUDED.email_status = 'valid' THEN NULL
                ELSE contacts.enrich_error
            END,
            first_name = COALESCE(EXCLUDED.first_name, contacts.first_name),
            last_name = COALESCE(EXCLUDED.last_name, contacts.last_name),
            job_title = COALESCE(EXCLUDED.job_title, contacts.job_title),
            company_name = COALESCE(EXCLUDED.company_name, contacts.company_name),
            company_domain = COALESCE(EXCLUDED.company_domain, contacts.company_domain),
            industry = COALESCE(EXCLUDED.industry, contacts.industry),
            location = COALESCE(EXCLUDED.location, contacts.location)
        WHERE contacts.source_person_id IS NULL
           OR contacts.email IS NULL
           OR contacts.email LIKE '%%*%%'
        RETURNING (xmax = 0) AS inserted
        """
        with self.db.connect() as conn:
            for contact in contacts:
                row = conn.execute(sql, _contact_defaults(contact)).fetchone()
                if row and row["inserted"]:
                    inserted += 1
                else:
                    skipped += 1
        return inserted, skipped

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,)).fetchone()

    def list_for_enrichment(self, limit: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM contacts
                WHERE status = 'new'
                   OR (status = 'enriched' AND (enriched_at IS NULL OR enriched_at < NOW() - INTERVAL '30 days'))
                ORDER BY created_at
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def dashboard_summary(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            statuses = conn.execute(
                "SELECT status::text AS status, COUNT(*) AS count FROM contacts GROUP BY status ORDER BY status"
            ).fetchall()
            events = conn.execute(
                """
                SELECT event_type::text AS event_type, COUNT(*) AS count
                FROM email_events
                WHERE occurred_at >= NOW() - INTERVAL '7 days'
                GROUP BY event_type
                ORDER BY event_type
                """
            ).fetchall()
            sent_today = conn.execute(
                "SELECT COUNT(*) AS count FROM email_events WHERE event_type = 'sent' AND occurred_at::date = CURRENT_DATE"
            ).fetchone()
            total = conn.execute("SELECT COUNT(*) AS count FROM contacts").fetchone()
        return {
            "total_contacts": int(total["count"]),
            "sent_today": int(sent_today["count"]),
            "statuses": {row["status"]: int(row["count"]) for row in statuses},
            "events_7d": {row["event_type"]: int(row["count"]) for row in events},
        }

    def list_contacts(self, *, status: str | None = None, search: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            validate_status(status)
            clauses.append("c.status = %s")
            params.append(status)
        if search:
            clauses.append(
                "(c.first_name ILIKE %s OR c.last_name ILIKE %s OR c.email ILIKE %s OR c.company_name ILIKE %s OR c.job_title ILIKE %s)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT c.id, c.first_name, c.last_name, c.email, c.email_status, c.job_title,
                       c.company_name, c.company_domain, c.industry, c.location, c.status::text,
                       c.sequence_step, c.last_contacted_at, c.replied_at, c.enriched_at,
                       c.enrich_error, c.notes, c.created_at, c.source_person_id, c.source,
                       COALESCE(ev.sent_count, 0) AS sent_count,
                       COALESCE(ev.opened_count, 0) AS opened_count,
                       COALESCE(ev.clicked_count, 0) AS clicked_count,
                       COALESCE(ev.replied_count, 0) AS replied_count,
                       COALESCE(ev.bounced_count, 0) AS bounced_count,
                       COALESCE(ev.unsubscribed_count, 0) AS unsubscribed_count,
                       ev.last_event_at,
                       ev.last_event_type
                FROM contacts c
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) FILTER (WHERE event_type = 'sent') AS sent_count,
                        COUNT(*) FILTER (WHERE event_type = 'opened') AS opened_count,
                        COUNT(*) FILTER (WHERE event_type = 'clicked') AS clicked_count,
                        COUNT(*) FILTER (WHERE event_type = 'replied') AS replied_count,
                        COUNT(*) FILTER (WHERE event_type = 'bounced') AS bounced_count,
                        COUNT(*) FILTER (WHERE event_type = 'unsubscribed') AS unsubscribed_count,
                        MAX(occurred_at) AS last_event_at,
                        (ARRAY_AGG(event_type::text ORDER BY occurred_at DESC))[1] AS last_event_type
                    FROM email_events
                    WHERE contact_id = c.id
                ) ev ON TRUE
                {where}
                ORDER BY c.created_at DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()

    def update_enrichment(self, contact_id: int, fields: dict[str, Any], *, error: str | None = None) -> None:
        status = "enriched" if fields.get("email_status") == "valid" else "new"
        payload = {
            "id": contact_id,
            "email": fields.get("email"),
            "email_status": fields.get("email_status", "unknown"),
            "company_size": fields.get("company_size"),
            "company_funding": fields.get("company_funding"),
            "industry": fields.get("industry"),
            "enrich_error": error,
            "status": status,
        }
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET email = COALESCE(%(email)s, email),
                    email_status = %(email_status)s,
                    company_size = COALESCE(%(company_size)s, company_size),
                    company_funding = COALESCE(%(company_funding)s, company_funding),
                    industry = COALESCE(%(industry)s, industry),
                    enriched_at = NOW(),
                    enrich_error = %(enrich_error)s,
                    status = %(status)s
                WHERE id = %(id)s
                """,
                payload,
            )

    def queue_contacts(self, limit: int) -> int:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                UPDATE contacts c
                SET status = 'queued'
                WHERE id IN (
                  SELECT id FROM contacts
                  WHERE status = 'enriched'
                    AND email_status = 'valid'
                    AND email IS NOT NULL
                    AND email NOT LIKE '%%*%%'
                    AND email LIKE '%%@%%'
                    AND NOT EXISTS (
                      SELECT 1 FROM blacklist b
                      WHERE b.email = contacts.email OR b.domain = contacts.company_domain
                    )
                  ORDER BY created_at
                  LIMIT %s
                )
                RETURNING c.id
                """,
                (limit,),
            ).fetchall()
            return len(rows)

    def due_for_sending(self, limit: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM contacts
                WHERE email_status = 'valid'
                  AND status IN ('queued', 'sent_1', 'sent_2')
                  AND NOT EXISTS (
                    SELECT 1 FROM blacklist b
                    WHERE b.email = contacts.email OR b.domain = contacts.company_domain
                  )
                ORDER BY last_contacted_at NULLS FIRST, created_at
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def sent_today_count(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM email_events WHERE event_type = 'sent' AND occurred_at::date = CURRENT_DATE"
            ).fetchone()
            return int(row["count"])

    def record_sent(self, contact_id: int, step: int, subject: str, message_id: str | None, metadata: dict[str, Any]) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO email_events(contact_id, sequence_step, event_type, email_subject, message_id, metadata)
                VALUES (%s, %s, 'sent', %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (contact_id, step, subject, message_id, json.dumps(metadata)),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """
                UPDATE contacts
                SET status = %s, sequence_step = %s, last_contacted_at = NOW()
                WHERE id = %s
                """,
                (f"sent_{step}", step, contact_id),
            )
            return True

    def mark_status(self, contact_id: int, status: str, *, notes: str | None = None) -> None:
        validate_status(status)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET status = %s,
                    replied_at = CASE WHEN %s = 'replied' THEN NOW() ELSE replied_at END,
                    notes = COALESCE(%s, notes)
                WHERE id = %s
                """,
                (status, status, notes, contact_id),
            )

    def record_event(self, contact_id: int, event_type: str, payload: dict[str, Any]) -> None:
        terminal = {"replied": "replied", "bounce": "bounced", "bounced": "bounced", "unsubscribe": "unsubscribed", "unsubscribed": "unsubscribed"}
        with self.db.connect() as conn:
            contact = conn.execute("SELECT sequence_step FROM contacts WHERE id = %s", (contact_id,)).fetchone()
            if not contact:
                return
            conn.execute(
                """
                INSERT INTO email_events(contact_id, sequence_step, event_type, metadata)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (contact_id, contact["sequence_step"] or 0, terminal.get(event_type, event_type), json.dumps(payload)),
            )
            if event_type in terminal:
                conn.execute("UPDATE contacts SET status = %s WHERE id = %s", (terminal[event_type], contact_id))

    def record_webhook_delivery(self, provider: str, event_type: str, payload: dict[str, Any], external_id: str | None = None) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO webhook_events(provider, event_type, payload, external_id)
                VALUES (%s, %s, %s::jsonb, %s)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (provider, event_type, json.dumps(payload), external_id),
            ).fetchone()
            return bool(row)

    def add_blacklist(self, *, email: str | None, domain: str | None, reason: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO blacklist(email, domain, reason)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (email, domain, reason),
            )

    def export_contacts(self, out: Path, status: str | None = None) -> int:
        params: tuple[Any, ...] = ()
        where = ""
        if status:
            validate_status(status)
            where = "WHERE status = %s"
            params = (status,)
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM contacts {where} ORDER BY created_at", params).fetchall()
        out.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            out.write_text("", encoding="utf-8")
            return 0
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)

    def export_contacts_csv_text(self, status: str | None = None) -> str:
        params: tuple[Any, ...] = ()
        where = ""
        if status:
            validate_status(status)
            where = "WHERE status = %s"
            params = (status,)
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM contacts {where} ORDER BY created_at", params).fetchall()
        if not rows:
            return ""
        import io

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()


def _contact_defaults(contact: dict[str, Any]) -> dict[str, Any]:
    return {
        "linkedin_url": contact["linkedin_url"],
        "first_name": contact.get("first_name"),
        "last_name": contact.get("last_name"),
        "email": contact.get("email"),
        "email_status": contact.get("email_status") or "unknown",
        "job_title": contact.get("job_title"),
        "company_name": contact.get("company_name"),
        "company_domain": contact.get("company_domain"),
        "industry": contact.get("industry"),
        "location": contact.get("location"),
        "company_size": contact.get("company_size"),
        "status": contact.get("status") or ("enriched" if contact.get("email_status") == "valid" else "new"),
        "source_person_id": contact.get("source_person_id"),
        "source": contact.get("source"),
    }
