from __future__ import annotations

import csv
import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .auth import hash_password, new_session_token, session_expires_at, verify_password
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

    def is_available(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

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

    def ensure_default_admin(self, username: str, password: str, display_name: str) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT id FROM sales_users WHERE username = %s", (username,)).fetchone()
            if row:
                return
            conn.execute(
                """
                INSERT INTO sales_users(username, password_hash, display_name, role)
                VALUES (%s, %s, %s, 'admin')
                """,
                (username, hash_password(password), display_name),
            )

    def authenticate_user(self, username: str, password: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            user = conn.execute(
                "SELECT * FROM sales_users WHERE username = %s AND active = TRUE",
                (username,),
            ).fetchone()
            if not user or not verify_password(password, user["password_hash"]):
                return None
            return user

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str,
        role: str = "sales",
        daily_source_limit: int = 100,
        daily_send_limit: int = 100,
        must_change_password: bool = True,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO sales_users(
                    username, password_hash, display_name, role, daily_source_limit, daily_send_limit,
                    must_change_password, password_changed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NULL ELSE NOW() END)
                RETURNING id, username, display_name, role, daily_source_limit, daily_send_limit,
                          active, must_change_password, created_at
                """,
                (username, hash_password(password), display_name, role, daily_source_limit, daily_send_limit, must_change_password, must_change_password),
            ).fetchone()

    def list_users(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT u.id, u.username, u.display_name, u.role, u.daily_source_limit, u.daily_send_limit,
                       u.active, u.must_change_password, u.created_at,
                       COALESCE(usage.source_count, 0) AS source_count_today,
                       COALESCE(usage.send_count, 0) AS send_count_today
                FROM sales_users u
                LEFT JOIN user_daily_usage usage
                  ON usage.user_id = u.id AND usage.usage_date = CURRENT_DATE
                ORDER BY u.id
                """
            ).fetchall()

    def update_user(
        self,
        user_id: int,
        *,
        display_name: str | None = None,
        role: str | None = None,
        daily_source_limit: int | None = None,
        daily_send_limit: int | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE sales_users
                SET display_name = COALESCE(%s, display_name),
                    role = COALESCE(%s, role),
                    daily_source_limit = COALESCE(%s, daily_source_limit),
                    daily_send_limit = COALESCE(%s, daily_send_limit),
                    active = COALESCE(%s, active)
                WHERE id = %s
                RETURNING id, username, display_name, role, daily_source_limit, daily_send_limit,
                          active, must_change_password, created_at
                """,
                (display_name, role, daily_source_limit, daily_send_limit, active, user_id),
            ).fetchone()

    def reset_user_password(self, user_id: int, password: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE sales_users
                SET password_hash = %s,
                    must_change_password = TRUE,
                    password_changed_at = NULL
                WHERE id = %s
                RETURNING id, username, display_name, role, daily_source_limit, daily_send_limit,
                          active, must_change_password, created_at
                """,
                (hash_password(password), user_id),
            ).fetchone()

    def change_own_password(self, user_id: int, current_password: str, new_password: str) -> dict[str, Any]:
        if len(new_password or "") < 12:
            raise RuntimeError("新密码至少 12 位")
        with self.db.connect() as conn:
            user = conn.execute("SELECT * FROM sales_users WHERE id = %s AND active = TRUE", (user_id,)).fetchone()
            if not user or not verify_password(current_password, user["password_hash"]):
                raise RuntimeError("当前密码不正确")
            return conn.execute(
                """
                UPDATE sales_users
                SET password_hash = %s,
                    must_change_password = FALSE,
                    password_changed_at = NOW()
                WHERE id = %s
                RETURNING id, username, display_name, role, daily_source_limit, daily_send_limit,
                          active, must_change_password, created_at
                """,
                (hash_password(new_password), user_id),
            ).fetchone()

    def create_session(self, user_id: int) -> str:
        token = new_session_token()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO user_sessions(token, user_id, expires_at) VALUES (%s, %s, %s)",
                (token, user_id, session_expires_at()),
            )
        return token

    def get_session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT u.*
                FROM user_sessions s
                JOIN sales_users u ON u.id = s.user_id
                WHERE s.token = %s
                  AND s.expires_at > NOW()
                  AND u.active = TRUE
                """,
                (token,),
            ).fetchone()

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        with self.db.connect() as conn:
            conn.execute("DELETE FROM user_sessions WHERE token = %s", (token,))

    def usage_for_user(self, user_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO user_daily_usage(user_id, usage_date)
                VALUES (%s, CURRENT_DATE)
                ON CONFLICT (user_id, usage_date) DO UPDATE SET user_id = EXCLUDED.user_id
                RETURNING usage_date, source_count, send_count
                """,
                (user_id,),
            ).fetchone()
            return row

    def consume_daily_quota(self, user_id: int, field: str, amount: int, limit: int) -> dict[str, Any]:
        if field not in {"source_count", "send_count"}:
            raise ValueError(f"Unsupported quota field: {field}")
        amount = max(0, int(amount))
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO user_daily_usage(user_id, usage_date)
                VALUES (%s, CURRENT_DATE)
                ON CONFLICT (user_id, usage_date) DO UPDATE SET user_id = EXCLUDED.user_id
                RETURNING usage_date, source_count, send_count
                """,
                (user_id,),
            ).fetchone()
            used = int(row[field] or 0)
            if used + amount > limit:
                raise RuntimeError(f"Daily quota exceeded: {used}/{limit}, requested {amount}")
            updated = conn.execute(
                f"""
                UPDATE user_daily_usage
                SET {field} = {field} + %s
                WHERE user_id = %s AND usage_date = CURRENT_DATE
                RETURNING usage_date, source_count, send_count
                """,
                (amount, user_id),
            ).fetchone()
            return updated

    def global_usage(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO global_daily_usage(usage_date)
                VALUES (CURRENT_DATE)
                ON CONFLICT (usage_date) DO UPDATE SET usage_date = EXCLUDED.usage_date
                RETURNING usage_date, source_count, send_count
                """
            ).fetchone()

    def consume_user_and_global_quota(
        self,
        user_id: int,
        field: str,
        amount: int,
        user_limit: int,
        global_limit: int,
    ) -> dict[str, Any]:
        if field not in {"source_count", "send_count"}:
            raise ValueError(f"Unsupported quota field: {field}")
        amount = max(0, int(amount))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_daily_usage(user_id, usage_date)
                VALUES (%s, CURRENT_DATE)
                ON CONFLICT (user_id, usage_date) DO NOTHING
                """,
                (user_id,),
            )
            conn.execute(
                """
                INSERT INTO global_daily_usage(usage_date)
                VALUES (CURRENT_DATE)
                ON CONFLICT (usage_date) DO NOTHING
                """
            )
            user_usage = conn.execute(
                """
                SELECT usage_date, source_count, send_count
                FROM user_daily_usage
                WHERE user_id = %s AND usage_date = CURRENT_DATE
                FOR UPDATE
                """,
                (user_id,),
            ).fetchone()
            global_usage = conn.execute(
                """
                SELECT usage_date, source_count, send_count
                FROM global_daily_usage
                WHERE usage_date = CURRENT_DATE
                FOR UPDATE
                """
            ).fetchone()
            if int(user_usage[field] or 0) + amount > user_limit:
                raise RuntimeError(f"user_daily_quota_exceeded:{field}:{user_usage[field]}/{user_limit}")
            if int(global_usage[field] or 0) + amount > global_limit:
                raise RuntimeError(f"global_daily_quota_exceeded:{field}:{global_usage[field]}/{global_limit}")
            updated_user = conn.execute(
                f"""
                UPDATE user_daily_usage
                SET {field} = {field} + %s
                WHERE user_id = %s AND usage_date = CURRENT_DATE
                RETURNING usage_date, source_count, send_count
                """,
                (amount, user_id),
            ).fetchone()
            updated_global = conn.execute(
                f"""
                UPDATE global_daily_usage
                SET {field} = {field} + %s
                WHERE usage_date = CURRENT_DATE
                RETURNING usage_date, source_count, send_count
                """,
                (amount,),
            ).fetchone()
            return {"user_usage": updated_user, "global_usage": updated_global}

    def consume_global_quota(self, field: str, amount: int, limit: int) -> dict[str, Any]:
        if field not in {"source_count", "send_count"}:
            raise ValueError(f"Unsupported quota field: {field}")
        amount = max(0, int(amount))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO global_daily_usage(usage_date)
                VALUES (CURRENT_DATE)
                ON CONFLICT (usage_date) DO NOTHING
                """
            )
            row = conn.execute(
                """
                SELECT usage_date, source_count, send_count
                FROM global_daily_usage
                WHERE usage_date = CURRENT_DATE
                FOR UPDATE
                """
            ).fetchone()
            if int(row[field] or 0) + amount > limit:
                raise RuntimeError(f"global_daily_quota_exceeded:{field}:{row[field]}/{limit}")
            return conn.execute(
                f"""
                UPDATE global_daily_usage
                SET {field} = {field} + %s
                WHERE usage_date = CURRENT_DATE
                RETURNING usage_date, source_count, send_count
                """,
                (amount,),
            ).fetchone()

    def ensure_sender_account(self, account: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO sender_accounts(name, email, provider, daily_limit, warmup_stage, active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (email) DO UPDATE
                SET name = EXCLUDED.name,
                    provider = EXCLUDED.provider,
                    daily_limit = EXCLUDED.daily_limit,
                    warmup_stage = EXCLUDED.warmup_stage
                RETURNING id, name, email, provider, daily_limit, warmup_stage, active, created_at
                """,
                (
                    account.get("name") or account.get("email"),
                    account.get("email"),
                    account.get("provider", "resend"),
                    int(account.get("daily_limit") or 100),
                    account.get("warmup_stage", "production"),
                ),
            ).fetchone()

    def sender_usage_today(self, sender_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO sender_daily_usage(sender_id, usage_date)
                VALUES (%s, CURRENT_DATE)
                ON CONFLICT (sender_id, usage_date) DO UPDATE SET sender_id = EXCLUDED.sender_id
                RETURNING sender_id, usage_date, send_count
                """,
                (sender_id,),
            ).fetchone()

    def sender_total_sent_today(self) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(send_count), 0) AS count FROM sender_daily_usage WHERE usage_date = CURRENT_DATE"
            ).fetchone()
            return int(row["count"] or 0)

    def record_sender_send(self, sender_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sender_daily_usage(sender_id, usage_date, send_count)
                VALUES (%s, CURRENT_DATE, 1)
                ON CONFLICT (sender_id, usage_date)
                DO UPDATE SET send_count = sender_daily_usage.send_count + 1
                """,
                (sender_id,),
            )

    def list_sender_accounts(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT s.id, s.name, s.email, s.provider, s.daily_limit, s.warmup_stage,
                       s.active, s.created_at, COALESCE(usage.send_count, 0) AS send_count_today
                FROM sender_accounts s
                LEFT JOIN sender_daily_usage usage
                  ON usage.sender_id = s.id AND usage.usage_date = CURRENT_DATE
                ORDER BY s.id
                """
            ).fetchall()

    def update_sender_account(
        self,
        sender_id: int,
        *,
        name: str | None = None,
        email: str | None = None,
        provider: str | None = None,
        daily_limit: int | None = None,
        warmup_stage: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE sender_accounts
                SET name = COALESCE(%s, name),
                    email = COALESCE(%s, email),
                    provider = COALESCE(%s, provider),
                    daily_limit = COALESCE(%s, daily_limit),
                    warmup_stage = COALESCE(%s, warmup_stage),
                    active = COALESCE(%s, active)
                WHERE id = %s
                RETURNING id, name, email, provider, daily_limit, warmup_stage, active, created_at
                """,
                (name, email, provider, daily_limit, warmup_stage, active, sender_id),
            ).fetchone()

    def upsert_contacts(self, contacts: Iterable[dict[str, Any]], *, owner_user_id: int | None = None) -> tuple[int, int]:
        inserted = skipped = 0
        sql = """
        INSERT INTO contacts (
          linkedin_url, first_name, last_name, email, email_status, job_title, company_name,
          company_domain, industry, location, company_size, status, source_person_id, source, owner_user_id, owner,
          email_candidates, lead_score, search_task_id, phone, phone_candidates
        ) VALUES (
          %(linkedin_url)s, %(first_name)s, %(last_name)s, %(email)s, %(email_status)s, %(job_title)s, %(company_name)s,
          %(company_domain)s, %(industry)s, %(location)s, %(company_size)s, %(status)s, %(source_person_id)s, %(source)s,
          %(owner_user_id)s, %(owner)s, %(email_candidates)s::jsonb, %(lead_score)s, %(search_task_id)s,
          %(phone)s, %(phone_candidates)s::jsonb
        )
        ON CONFLICT (linkedin_url) DO UPDATE
        SET source_person_id = COALESCE(EXCLUDED.source_person_id, contacts.source_person_id),
            owner_user_id = COALESCE(contacts.owner_user_id, EXCLUDED.owner_user_id),
            owner = COALESCE(contacts.owner, EXCLUDED.owner),
            lead_score = COALESCE(EXCLUDED.lead_score, contacts.lead_score),
            search_task_id = COALESCE(EXCLUDED.search_task_id, contacts.search_task_id),
            email_candidates = CASE
                WHEN EXCLUDED.email_candidates <> '[]'::jsonb THEN EXCLUDED.email_candidates
                ELSE contacts.email_candidates
            END,
            email = CASE
                WHEN EXCLUDED.email IS NOT NULL AND EXCLUDED.email NOT LIKE '%%*%%' THEN EXCLUDED.email
                ELSE contacts.email
            END,
            phone = COALESCE(EXCLUDED.phone, contacts.phone),
            phone_candidates = CASE
                WHEN EXCLUDED.phone_candidates <> '[]'::jsonb THEN EXCLUDED.phone_candidates
                ELSE contacts.phone_candidates
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
                defaults = _contact_defaults(contact)
                defaults["owner_user_id"] = owner_user_id or contact.get("owner_user_id")
                defaults["owner"] = contact.get("owner")
                defaults["email_candidates"] = json.dumps(contact.get("email_candidates") or [])
                defaults["phone_candidates"] = json.dumps(contact.get("phone_candidates") or [])
                defaults["lead_score"] = contact.get("lead_score")
                defaults["search_task_id"] = contact.get("search_task_id")
                row = conn.execute(sql, defaults).fetchone()
                if row and row["inserted"]:
                    inserted += 1
                else:
                    skipped += 1
        return inserted, skipped

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,)).fetchone()

    def get_contact_by_linkedin_url(self, linkedin_url: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM contacts WHERE linkedin_url = %s", (linkedin_url,)).fetchone()

    def find_duplicate_contact(self, contact: dict[str, Any]) -> dict[str, Any] | None:
        first = (contact.get("first_name") or "").strip()
        last = (contact.get("last_name") or "").strip()
        company = (contact.get("company_name") or "").strip()
        domain = (contact.get("company_domain") or "").strip().lower()
        if not first or not last or not (company or domain):
            return None
        clauses = ["LOWER(first_name) = LOWER(%s)", "LOWER(last_name) = LOWER(%s)"]
        params: list[Any] = [first, last]
        if domain:
            clauses.append("LOWER(COALESCE(company_domain, '')) = LOWER(%s)")
            params.append(domain)
        else:
            clauses.append("LOWER(COALESCE(company_name, '')) = LOWER(%s)")
            params.append(company)
        with self.db.connect() as conn:
            return conn.execute(
                f"SELECT * FROM contacts WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 1",
                tuple(params),
            ).fetchone()

    def get_contact_for_user(self, contact_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
        if user.get("role") == "admin":
            return self.get_contact(contact_id)
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM contacts WHERE id = %s AND owner_user_id = %s",
                (contact_id, user["id"]),
            ).fetchone()

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

    def dashboard_summary(self, *, user: dict[str, Any] | None = None) -> dict[str, Any]:
        owner_filter, owner_params = self._owner_filter("c", user)
        with self.db.connect() as conn:
            statuses = conn.execute(
                f"SELECT status::text AS status, COUNT(*) AS count FROM contacts c {owner_filter} GROUP BY status ORDER BY status",
                tuple(owner_params),
            ).fetchall()
            events = conn.execute(
                f"""
                SELECT e.event_type::text AS event_type, COUNT(*) AS count
                FROM email_events e
                JOIN contacts c ON c.id = e.contact_id
                WHERE e.occurred_at >= NOW() - INTERVAL '7 days'
                  {self._owner_filter_sql("c", user, prefix="AND")}
                GROUP BY event_type
                ORDER BY event_type
                """,
                tuple(owner_params),
            ).fetchall()
            sent_today = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM email_events e
                JOIN contacts c ON c.id = e.contact_id
                WHERE e.event_type = 'sent' AND e.occurred_at::date = CURRENT_DATE
                  {self._owner_filter_sql("c", user, prefix="AND")}
                """,
                tuple(owner_params),
            ).fetchone()
            total = conn.execute(f"SELECT COUNT(*) AS count FROM contacts c {owner_filter}", tuple(owner_params)).fetchone()
            lifecycle = conn.execute(
                f"""
                SELECT lifecycle_stage, COUNT(*) AS count
                FROM contacts c
                {owner_filter}
                GROUP BY lifecycle_stage
                ORDER BY lifecycle_stage
                """,
                tuple(owner_params),
            ).fetchall()
            disposition = conn.execute(
                f"""
                SELECT disposition, COUNT(*) AS count
                FROM contacts c
                {owner_filter}
                GROUP BY disposition
                ORDER BY disposition
                """,
                tuple(owner_params),
            ).fetchall()
        return {
            "total_contacts": int(total["count"]),
            "sent_today": int(sent_today["count"]),
            "statuses": {row["status"]: int(row["count"]) for row in statuses},
            "events_7d": {row["event_type"]: int(row["count"]) for row in events},
            "lifecycle": {row["lifecycle_stage"]: int(row["count"]) for row in lifecycle},
            "dispositions": {row["disposition"]: int(row["count"]) for row in disposition},
        }

    def list_contacts(
        self,
        *,
        status: str | None = None,
        search: str | None = None,
        limit: int = 100,
        user: dict[str, Any] | None = None,
        filter_key: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user and user.get("role") != "admin":
            clauses.append("c.owner_user_id = %s")
            params.append(user["id"])
        if status:
            validate_status(status)
            clauses.append("c.status = %s")
            params.append(status)
        if filter_key:
            self._append_contact_filter(clauses, filter_key)
        if search:
            clauses.append(
                "(c.first_name ILIKE %s OR c.last_name ILIKE %s OR c.email ILIKE %s OR c.phone ILIKE %s OR c.company_name ILIKE %s OR c.job_title ILIKE %s)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like, like])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT c.id, c.linkedin_url, c.first_name, c.last_name, c.email, c.email_status, c.job_title,
                       c.company_name, c.company_domain, c.industry, c.location, c.status::text,
                       c.sequence_step, c.last_contacted_at, c.replied_at, c.enriched_at,
                       c.enrich_error, c.notes, c.created_at, c.source_person_id, c.source,
                       c.email_source, c.email_confidence, c.email_candidates, c.phone, c.phone_candidates,
                       c.social_profiles, c.social_enriched_at, c.social_error,
                       c.outreach_stage, c.lifecycle_stage, c.disposition, c.next_action_at,
                       c.owner, c.owner_user_id, c.lost_reason, c.profile_summary, c.profile_insights, c.profile_updated_at,
                       c.lead_score, c.search_task_id,
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

    def _owner_filter(self, alias: str, user: dict[str, Any] | None = None, *, prefix: str = "WHERE") -> tuple[str, list[Any]]:
        if user and user.get("role") != "admin":
            return f"{prefix} {alias}.owner_user_id = %s", [user["id"]]
        return "", []

    def _owner_filter_sql(self, alias: str, user: dict[str, Any] | None = None, *, prefix: str = "WHERE") -> str:
        if user and user.get("role") != "admin":
            return f"{prefix} {alias}.owner_user_id = %s"
        return ""

    def _append_contact_filter(self, clauses: list[str], filter_key: str) -> None:
        filters = {
            "mine": "c.owner_user_id IS NOT NULL",
            "needs_enrichment": "(c.email_status IS DISTINCT FROM 'valid' OR c.email IS NULL)",
            "ready_to_send": "c.email_status = 'valid' AND c.status = 'enriched'",
            "opened_no_reply": "COALESCE(ev.opened_count, 0) > 0 AND c.status NOT IN ('replied', 'bounced', 'unsubscribed')",
            "replied": "(c.status = 'replied' OR COALESCE(ev.replied_count, 0) > 0)",
            "bounced": "(c.status = 'bounced' OR COALESCE(ev.bounced_count, 0) > 0)",
            "second_touch_due": "c.status = 'sent_1'",
            "third_touch_due": "c.status = 'sent_2'",
            "waiting_pool": "c.lifecycle_stage = 'waiting_pool'",
            "abandoned": "(c.lifecycle_stage = 'abandoned' OR c.disposition = 'abandoned')",
        }
        clause = filters.get(filter_key)
        if clause:
            clauses.append(clause)

    def operations_report(self, *, user: dict[str, Any] | None = None) -> dict[str, Any]:
        is_admin = bool(user and user.get("role") == "admin")
        owner_filter, owner_params = self._owner_filter("c", user, prefix="AND")
        with self.db.connect() as conn:
            totals = conn.execute(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE c.created_at::date = CURRENT_DATE) AS new_contacts_today,
                  COUNT(*) FILTER (WHERE c.email_status = 'valid' AND c.enriched_at::date = CURRENT_DATE) AS valid_emails_today,
                  COUNT(*) FILTER (WHERE c.status = 'queued') AS queued,
                  COUNT(*) FILTER (WHERE c.status = 'bounced') AS bounced,
                  COUNT(*) FILTER (WHERE c.status = 'replied') AS replied
                FROM contacts c
                WHERE TRUE {owner_filter}
                """,
                tuple(owner_params),
            ).fetchone()
            events = conn.execute(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE e.event_type = 'sent') AS sent_today,
                  COUNT(*) FILTER (WHERE e.event_type = 'opened') AS opened_today,
                  COUNT(*) FILTER (WHERE e.event_type = 'clicked') AS clicked_today,
                  COUNT(*) FILTER (WHERE e.event_type = 'replied') AS replied_events_today,
                  COUNT(*) FILTER (WHERE e.event_type = 'bounced') AS bounced_events_today,
                  COUNT(DISTINCT e.contact_id) FILTER (WHERE e.event_type = 'opened' AND c.status NOT IN ('replied', 'bounced', 'unsubscribed')) AS opened_no_reply
                FROM email_events e
                JOIN contacts c ON c.id = e.contact_id
                WHERE e.occurred_at::date = CURRENT_DATE {owner_filter}
                """,
                tuple(owner_params),
            ).fetchone()
            user_scope = "" if is_admin else "WHERE u.id = %s"
            user_scope_params = () if is_admin else (user["id"],)
            by_user = conn.execute(
                f"""
                SELECT u.id, u.username, u.display_name, u.role, u.active,
                       u.daily_source_limit, u.daily_send_limit,
                       COALESCE(usage.source_count, 0) AS source_count_today,
                       COALESCE(usage.send_count, 0) AS send_count_today,
                       COUNT(c.id) AS owned_contacts
                FROM sales_users u
                LEFT JOIN user_daily_usage usage
                  ON usage.user_id = u.id AND usage.usage_date = CURRENT_DATE
                LEFT JOIN contacts c ON c.owner_user_id = u.id
                {user_scope}
                GROUP BY u.id, usage.source_count, usage.send_count
                ORDER BY u.id
                """,
                user_scope_params,
            ).fetchall()
            provider_stats = []
            if is_admin:
                provider_stats = conn.execute(
                    """
                    SELECT provider, stat_date, calls, candidates, valid_candidates, selected, errors, credits_used, last_error
                    FROM email_provider_stats
                    WHERE stat_date >= CURRENT_DATE - INTERVAL '7 days'
                    ORDER BY stat_date DESC, provider
                    """
                ).fetchall()
            failures = conn.execute(
                f"""
                SELECT reason, COUNT(*) AS count
                FROM (
                  SELECT COALESCE(NULLIF(enrich_error, ''), '邮箱/富化无错误') AS reason
                  FROM contacts c
                  WHERE enrich_error IS NOT NULL {owner_filter}
                  UNION ALL
                  SELECT '退信需处理' AS reason
                  FROM contacts c
                  WHERE c.status = 'bounced' {owner_filter}
                ) items
                GROUP BY reason
                ORDER BY count DESC
                LIMIT 20
                """,
                tuple(owner_params + owner_params),
            ).fetchall()
        return {
            "totals": {key: int(value or 0) for key, value in dict(totals).items()},
            "events": {key: int(value or 0) for key, value in dict(events).items()},
            "by_user": by_user,
            "provider_stats": provider_stats,
            "failures": failures,
            "scope": "team" if is_admin else "self",
        }

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
            "email_source": fields.get("email_source"),
            "email_confidence": fields.get("email_confidence"),
            "email_candidates": fields.get("email_candidates", []),
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
                    email_source = COALESCE(%(email_source)s, email_source),
                    email_confidence = COALESCE(%(email_confidence)s, email_confidence),
                    email_candidates = CASE
                        WHEN %(email_candidates)s::jsonb = '[]'::jsonb THEN email_candidates
                        ELSE %(email_candidates)s::jsonb
                    END,
                    status = %(status)s
                WHERE id = %(id)s
                """,
                {**payload, "email_candidates": json.dumps(payload["email_candidates"])},
            )

    def create_lead_search_task(
        self,
        *,
        criteria: dict[str, Any],
        provider: str,
        requested_limit: int,
        created_by_user_id: int,
        owner_user_id: int,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO lead_search_tasks(criteria, provider, requested_limit, created_by_user_id, owner_user_id)
                VALUES (%s::jsonb, %s, %s, %s, %s)
                RETURNING id, criteria, provider, status, requested_limit, query_count, result_count,
                          promoted_count, skipped_count, error, created_at, completed_at
                """,
                (json.dumps(criteria), provider, requested_limit, created_by_user_id, owner_user_id),
            ).fetchone()

    def complete_lead_search_task(
        self,
        task_id: int,
        *,
        query_count: int,
        result_count: int,
        promoted_count: int,
        skipped_count: int,
        error: str | None = None,
    ) -> None:
        status = "failed" if error else "completed"
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE lead_search_tasks
                SET status = %s,
                    query_count = %s,
                    result_count = %s,
                    promoted_count = %s,
                    skipped_count = %s,
                    error = %s,
                    completed_at = NOW()
                WHERE id = %s
                """,
                (status, query_count, result_count, promoted_count, skipped_count, error, task_id),
            )

    def list_lead_search_tasks(self, *, user: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if user.get("role") != "admin":
            where = "WHERE owner_user_id = %s"
            params.append(user["id"])
        params.append(limit)
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT id, criteria, provider, status, requested_limit, query_count, result_count,
                       promoted_count, skipped_count, error, created_at, completed_at
                FROM lead_search_tasks
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()

    def create_lead_search_result(self, task_id: int, parsed: dict[str, Any], *, status: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO lead_search_results(
                    task_id, raw_title, raw_snippet, raw_url, linkedin_url, first_name, last_name,
                    job_title, company_name, company_domain, location, lead_score, email_candidates, status, failure_reason
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                RETURNING id, task_id, raw_title, raw_snippet, raw_url, linkedin_url, first_name, last_name,
                          job_title, company_name, company_domain, location, lead_score, email_candidates,
                          promoted_contact_id, status, failure_reason, created_at
                """,
                (
                    task_id,
                    parsed.get("raw_title"),
                    parsed.get("raw_snippet"),
                    parsed.get("raw_url"),
                    parsed.get("linkedin_url"),
                    parsed.get("first_name"),
                    parsed.get("last_name"),
                    parsed.get("job_title"),
                    parsed.get("company_name"),
                    parsed.get("company_domain"),
                    parsed.get("location"),
                    int(parsed.get("lead_score") or 0),
                    json.dumps(parsed.get("email_candidates") or []),
                    status,
                    parsed.get("failure_reason"),
                ),
            ).fetchone()

    def list_lead_search_results(self, task_id: int, *, user: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT r.id, r.task_id, r.raw_title, r.raw_snippet, r.raw_url, r.linkedin_url,
                       r.first_name, r.last_name, r.job_title, r.company_name, r.company_domain,
                       r.location, r.lead_score, r.email_candidates, r.promoted_contact_id,
                       r.status, r.failure_reason, r.created_at
                FROM lead_search_results r
                JOIN lead_search_tasks t ON t.id = r.task_id
                WHERE r.task_id = %s
                  AND (%s = 'admin' OR t.owner_user_id = %s)
                ORDER BY r.lead_score DESC, r.created_at
                LIMIT %s
                """,
                (task_id, user.get("role"), user["id"], limit),
            ).fetchall()

    def get_lead_search_result_for_user(self, result_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT r.*, t.owner_user_id
                FROM lead_search_results r
                JOIN lead_search_tasks t ON t.id = r.task_id
                WHERE r.id = %s
                  AND (%s = 'admin' OR t.owner_user_id = %s)
                """,
                (result_id, user.get("role"), user["id"]),
            ).fetchone()

    def mark_lead_search_result_promoted(self, result_id: int, contact_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE lead_search_results
                SET promoted_contact_id = %s,
                    status = 'promoted'
                WHERE id = %s
                """,
                (contact_id, result_id),
            )

    def update_lead_search_result_status(self, result_id: int, status: str, failure_reason: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE lead_search_results SET status = %s, failure_reason = COALESCE(%s, failure_reason) WHERE id = %s",
                (status, failure_reason, result_id),
            )

    def email_patterns_for_domain(self, domain: str) -> list[str]:
        normalized = (domain or "").lower().removeprefix("www.")
        if not normalized:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT email, first_name, last_name
                FROM contacts
                WHERE company_domain = %s
                  AND email_status = 'valid'
                  AND email IS NOT NULL
                  AND email NOT LIKE '%%*%%'
                ORDER BY enriched_at DESC NULLS LAST, created_at DESC
                LIMIT 50
                """,
                (normalized,),
            ).fetchall()
        patterns: list[str] = []
        for row in rows:
            pattern = _infer_email_pattern(row["email"], row.get("first_name"), row.get("last_name"))
            if pattern and pattern not in patterns:
                patterns.append(pattern)
        return patterns

    def adopt_email_candidate(self, contact_id: int, selected: dict[str, Any]) -> None:
        email = str(selected.get("email") or "").strip().lower()
        if not email:
            raise ValueError("email candidate is empty")
        with self.db.connect() as conn:
            contact = conn.execute("SELECT email_candidates FROM contacts WHERE id = %s", (contact_id,)).fetchone()
            candidates = contact["email_candidates"] if contact and isinstance(contact["email_candidates"], list) else []
            updated_candidates = []
            for item in candidates:
                if str(item.get("email") or "").lower() == email:
                    updated_candidates.append({**item, **selected, "status": selected.get("status") or "valid", "adopted": True})
                else:
                    updated_candidates.append(item)
            if not updated_candidates:
                updated_candidates = [{**selected, "email": email, "adopted": True}]
            conn.execute(
                """
                UPDATE contacts
                SET email = %s,
                    email_status = %s,
                    email_source = %s,
                    email_confidence = %s,
                    email_candidates = %s::jsonb,
                    status = 'enriched',
                    enriched_at = NOW(),
                    enrich_error = NULL
                WHERE id = %s
                """,
                (
                    email,
                    selected.get("status") or "valid",
                    selected.get("source"),
                    int(selected.get("confidence") or 0),
                    json.dumps(updated_candidates),
                    contact_id,
                ),
            )

    def update_contacts_phone_from_search_task(
        self,
        search_task_id: int,
        *,
        phone: str | None = None,
        phone_candidates: list[dict[str, Any]] | None = None,
        owner_user_id: int | None = None,
    ) -> int:
        if not phone and not phone_candidates:
            return 0
        clauses = ["search_task_id = %s"]
        where_params: list[Any] = [search_task_id]
        if owner_user_id:
            clauses.append("owner_user_id = %s")
            where_params.append(owner_user_id)
        candidates_json = json.dumps(phone_candidates or [])
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                UPDATE contacts
                SET phone = COALESCE(phone, %s),
                    phone_candidates = CASE
                      WHEN %s::jsonb = '[]'::jsonb THEN phone_candidates
                      ELSE %s::jsonb
                    END
                WHERE {" AND ".join(clauses)}
                RETURNING id
                """,
                (phone, candidates_json, candidates_json, *where_params),
            ).fetchall()
            return len(rows)

    def record_email_provider_stat(
        self,
        provider: str,
        *,
        calls: int = 0,
        candidates: int = 0,
        valid_candidates: int = 0,
        selected: int = 0,
        errors: int = 0,
        credits_used: int = 0,
        last_error: str | None = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO email_provider_stats(
                    provider, stat_date, calls, candidates, valid_candidates, selected, errors, credits_used, last_error
                )
                VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider, stat_date) DO UPDATE
                SET calls = email_provider_stats.calls + EXCLUDED.calls,
                    candidates = email_provider_stats.candidates + EXCLUDED.candidates,
                    valid_candidates = email_provider_stats.valid_candidates + EXCLUDED.valid_candidates,
                    selected = email_provider_stats.selected + EXCLUDED.selected,
                    errors = email_provider_stats.errors + EXCLUDED.errors,
                    credits_used = email_provider_stats.credits_used + EXCLUDED.credits_used,
                    last_error = COALESCE(EXCLUDED.last_error, email_provider_stats.last_error),
                    updated_at = NOW()
                """,
                (provider, calls, candidates, valid_candidates, selected, errors, credits_used, last_error),
            )

    def list_for_social_enrichment(self, limit: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM contacts
                WHERE (
                    social_enriched_at IS NULL
                    OR social_enriched_at < NOW() - INTERVAL '30 days'
                  )
                  AND (
                    linkedin_url LIKE 'http%%'
                    OR (email_status = 'valid' AND email IS NOT NULL)
                    OR (first_name IS NOT NULL AND company_name IS NOT NULL)
                  )
                ORDER BY social_enriched_at NULLS FIRST, created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def update_social_profiles(self, contact_id: int, profiles: dict[str, Any], *, error: str | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET social_profiles = COALESCE(%s::jsonb, '{}'::jsonb),
                    social_enriched_at = NOW(),
                    social_error = %s
                WHERE id = %s
                """,
                (json.dumps(profiles), error, contact_id),
            )

    def lifecycle_summary(self, *, user: dict[str, Any] | None = None) -> dict[str, Any]:
        owner_where, owner_params = self._owner_filter("c", user)
        owner_and, owner_and_params = self._owner_filter("c", user, prefix="AND")
        with self.db.connect() as conn:
            stages = conn.execute(
                f"""
                SELECT lifecycle_stage, COUNT(*) AS count
                FROM contacts c
                {owner_where}
                GROUP BY lifecycle_stage
                """,
                tuple(owner_params),
            ).fetchall()
            outreach = conn.execute(
                f"""
                SELECT outreach_stage, COUNT(*) AS count
                FROM contacts c
                {owner_where}
                GROUP BY outreach_stage
                """,
                tuple(owner_params),
            ).fetchall()
            action_rows = conn.execute(
                f"""
                SELECT id, first_name, last_name, company_name, lifecycle_stage, disposition,
                       next_action_at, profile_summary
                FROM contacts c
                WHERE disposition IN ('active', 'waiting')
                  AND (next_action_at IS NULL OR next_action_at <= NOW() + INTERVAL '7 days')
                  {owner_and}
                ORDER BY next_action_at NULLS FIRST, created_at DESC
                LIMIT 12
                """,
                tuple(owner_and_params),
            ).fetchall()
        return {
            "stages": {row["lifecycle_stage"]: int(row["count"]) for row in stages},
            "outreach": {row["outreach_stage"]: int(row["count"]) for row in outreach},
            "actions": action_rows,
        }

    def update_lifecycle(
        self,
        contact_id: int,
        *,
        lifecycle_stage: str | None = None,
        disposition: str | None = None,
        next_action_at: str | None = None,
        notes: str | None = None,
        lost_reason: str | None = None,
        owner: str | None = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET lifecycle_stage = COALESCE(%s, lifecycle_stage),
                    disposition = COALESCE(%s, disposition),
                    next_action_at = COALESCE(%s::timestamptz, next_action_at),
                    notes = COALESCE(%s, notes),
                    lost_reason = COALESCE(%s, lost_reason),
                    owner = COALESCE(%s, owner)
                WHERE id = %s
                """,
                (lifecycle_stage, disposition, next_action_at, notes, lost_reason, owner, contact_id),
            )

    def update_profile_summary(self, contact_id: int, summary: str, insights: dict[str, Any] | None = None) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET profile_summary = %s,
                    profile_insights = COALESCE(%s::jsonb, profile_insights),
                    profile_updated_at = NOW()
                WHERE id = %s
                """,
                (summary, json.dumps(insights) if insights is not None else None, contact_id),
            )

    def contact_detail(self, contact_id: int, *, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        contact = self.get_contact_for_user(contact_id, user) if user else self.get_contact(contact_id)
        if not contact:
            return None
        return {"contact": contact, "activities": self.list_lifecycle_activities(contact_id)}

    def list_lifecycle_activities(self, contact_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT id, contact_id, lifecycle_stage, activity_type, title, content,
                       ai_analysis, created_by, created_at
                FROM lifecycle_activities
                WHERE contact_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (contact_id, limit),
            ).fetchall()

    def add_lifecycle_activity(
        self,
        contact_id: int,
        *,
        lifecycle_stage: str,
        activity_type: str,
        content: str,
        title: str | None = None,
        created_by: str | None = None,
        ai_analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO lifecycle_activities(
                    contact_id, lifecycle_stage, activity_type, title, content, created_by, ai_analysis
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id, contact_id, lifecycle_stage, activity_type, title, content,
                          ai_analysis, created_by, created_at
                """,
                (contact_id, lifecycle_stage, activity_type, title, content, created_by, json.dumps(ai_analysis or {})),
            ).fetchone()
            conn.execute(
                """
                UPDATE contacts
                SET lifecycle_stage = %s,
                    notes = COALESCE(%s, notes)
                WHERE id = %s
                """,
                (lifecycle_stage, content[:500], contact_id),
            )
            return row

    def update_lifecycle_activity_analysis(self, activity_id: int, analysis: dict[str, Any]) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE lifecycle_activities
                SET ai_analysis = %s::jsonb
                WHERE id = %s
                """,
                (json.dumps(analysis), activity_id),
            )

    def queue_contacts(self, limit: int, *, user: dict[str, Any] | None = None) -> int:
        owner_filter, owner_params = self._owner_filter("contacts", user, prefix="AND")
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
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
                    {owner_filter}
                  ORDER BY created_at
                  LIMIT %s
                )
                RETURNING c.id
                """,
                tuple(owner_params + [limit]),
            ).fetchall()
            return len(rows)

    def queue_contact(self, contact_id: int, *, user: dict[str, Any] | None = None) -> bool:
        owner_filter, owner_params = self._owner_filter("c", user, prefix="AND")
        with self.db.connect() as conn:
            row = conn.execute(
                f"""
                UPDATE contacts c
                SET status = 'queued'
                WHERE c.id = %s
                  AND c.status = 'enriched'
                  AND c.email_status = 'valid'
                  AND c.email IS NOT NULL
                  AND c.email NOT LIKE '%%*%%'
                  AND c.email LIKE '%%@%%'
                  AND NOT EXISTS (
                    SELECT 1 FROM blacklist b
                    WHERE b.email = c.email OR b.domain = c.company_domain
                  )
                  {owner_filter}
                RETURNING c.id
                """,
                tuple([contact_id] + owner_params),
            ).fetchone()
            return bool(row)

    def due_for_sending(self, limit: int, *, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        owner_filter, owner_params = self._owner_filter("contacts", user, prefix="AND")
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT * FROM contacts
                WHERE email_status = 'valid'
                  AND status IN ('queued', 'sent_1', 'sent_2')
                  AND NOT EXISTS (
                    SELECT 1 FROM blacklist b
                    WHERE b.email = contacts.email OR b.domain = contacts.company_domain
                  )
                  {owner_filter}
                ORDER BY last_contacted_at NULLS FIRST, created_at
                LIMIT %s
                """,
                tuple(owner_params + [limit]),
            ).fetchall()

    def due_contact_for_sending(self, contact_id: int, *, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        owner_filter, owner_params = self._owner_filter("contacts", user, prefix="AND")
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT * FROM contacts
                WHERE id = %s
                  AND email_status = 'valid'
                  AND status IN ('queued', 'sent_1', 'sent_2')
                  AND NOT EXISTS (
                    SELECT 1 FROM blacklist b
                    WHERE b.email = contacts.email OR b.domain = contacts.company_domain
                  )
                  {owner_filter}
                """,
                tuple([contact_id] + owner_params),
            ).fetchone()

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

    def record_manual_sent(self, contact_id: int, step: int, subject: str, message_id: str | None, metadata: dict[str, Any]) -> bool:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO email_events(contact_id, sequence_step, event_type, email_subject, message_id, metadata)
                VALUES (%s, %s, 'sent', %s, %s, %s::jsonb)
                RETURNING id
                """,
                (contact_id, step, subject, message_id, json.dumps(metadata)),
            ).fetchone()
            conn.execute(
                """
                UPDATE contacts
                SET sequence_step = GREATEST(sequence_step, %s),
                    last_contacted_at = NOW(),
                    status = CASE
                        WHEN %s <= 1 THEN 'sent_1'::contact_status
                        WHEN %s = 2 THEN 'sent_2'::contact_status
                        ELSE 'sent_3'::contact_status
                    END
                WHERE id = %s
                """,
                (step, step, step, contact_id),
            )
            return bool(row)

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

    def export_contacts_csv_text(self, status: str | None = None, *, user: dict[str, Any] | None = None) -> str:
        clauses: list[str] = []
        params: list[Any] = []
        if user and user.get("role") != "admin":
            clauses.append("owner_user_id = %s")
            params.append(user["id"])
        if status:
            validate_status(status)
            clauses.append("status = %s")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.db.connect() as conn:
            rows = conn.execute(f"SELECT * FROM contacts {where} ORDER BY created_at", tuple(params)).fetchall()
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
        "phone": contact.get("phone"),
        "phone_candidates": contact.get("phone_candidates") or [],
    }


def _infer_email_pattern(email: str, first_name: str | None, last_name: str | None) -> str | None:
    if "@" not in str(email or ""):
        return None
    first = _email_token(first_name)
    last = _email_token(last_name)
    local = email.split("@", 1)[0].lower()
    if first and last:
        if local == f"{first}.{last}":
            return "{first}.{last}"
        if local == f"{first}{last}":
            return "{first}{last}"
        if local == f"{first[0]}.{last}":
            return "{f}.{last}"
        if local == f"{first}{last[0]}":
            return "{first}{l}"
        if local == f"{last}.{first}":
            return "{last}.{first}"
    if first and local == first:
        return "{first}"
    return None


def _email_token(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
