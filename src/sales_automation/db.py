from __future__ import annotations

import csv
import hashlib
import json
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .auth import hash_password, new_session_token, session_expires_at, verify_password
from .config import AppConfig
from .customer_intelligence import build_customer_profile
from .outbound_quality import assess_icp, calibration_summary, default_icp_profile, summarize_experiment
from .sabcd import stage_from_payload
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
        self._actor = ContextVar(
            f"sales_database_actor_{id(self)}",
            default={"id": None, "role": "anonymous"},
        )

    def bind_actor(self, user: dict[str, Any] | None) -> None:
        if not user:
            self._actor.set({"id": None, "role": "anonymous"})
            return
        role = "admin" if user.get("role") == "admin" else "sales"
        self._actor.set({"id": int(user["id"]), "role": role})

    def bind_system_actor(self) -> None:
        self._actor.set({"id": None, "role": "system"})

    def is_available(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    @contextmanager
    def connect(self, *, enforce_security: bool = True):
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
            if enforce_security:
                runtime_role = conn.execute(
                    "SELECT 1 AS available FROM pg_roles WHERE rolname = 'sales_automation_runtime'"
                ).fetchone()
                if runtime_role:
                    conn.execute("SET LOCAL ROLE sales_automation_runtime")
            actor = self._actor.get()
            conn.execute(
                "SELECT set_config('sales.actor_id', %s, true), set_config('sales.actor_role', %s, true)",
                (str(actor["id"] or ""), actor["role"]),
            )
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def migrate(self, migration_dir: Path = Path("migrations")) -> list[str]:
        applied: list[str] = []
        with self.connect(enforce_security=False) as conn:
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
        daily_send_limit: int = 200,
        reply_to_email: str | None = None,
        sender_alias_localpart: str | None = None,
        must_change_password: bool = True,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO sales_users(
                    username, password_hash, display_name, role, daily_source_limit, daily_send_limit,
                    reply_to_email, sender_alias_localpart, must_change_password, password_changed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NULL ELSE NOW() END)
                RETURNING id, username, display_name, role, daily_source_limit, daily_send_limit,
                          reply_to_email, sender_alias_localpart, active, must_change_password, created_at
                """,
                (
                    username,
                    hash_password(password),
                    display_name,
                    role,
                    daily_source_limit,
                    daily_send_limit,
                    _clean_optional_email(reply_to_email),
                    _clean_optional_alias(sender_alias_localpart),
                    must_change_password,
                    must_change_password,
                ),
            ).fetchone()

    def vps_login_user(
        self,
        *,
        odoo_user_id: int,
        username: str,
        display_name: str,
        email: str | None = None,
        vps_barcode: str | None = None,
        department: str | None = None,
        role: str = "sales",
        daily_source_limit: int = 100,
        daily_send_limit: int = 200,
        auto_create: bool = True,
    ) -> dict[str, Any]:
        login_name = _vps_username(username, odoo_user_id)
        with self.db.connect() as conn:
            user = conn.execute(
                """
                SELECT *
                FROM sales_users
                WHERE odoo_user_id = %s
                   OR (%s IS NOT NULL AND vps_barcode = %s)
                   OR (%s IS NOT NULL AND LOWER(reply_to_email) = LOWER(%s))
                   OR LOWER(username) = LOWER(%s)
                ORDER BY
                  CASE
                    WHEN odoo_user_id = %s THEN 0
                    WHEN %s IS NOT NULL AND vps_barcode = %s THEN 1
                    WHEN %s IS NOT NULL AND LOWER(reply_to_email) = LOWER(%s) THEN 2
                    ELSE 3
                  END,
                  id
                LIMIT 1
                """,
                (
                    odoo_user_id,
                    _blank_to_none(vps_barcode),
                    _blank_to_none(vps_barcode),
                    _blank_to_none(email),
                    _blank_to_none(email),
                    login_name,
                    odoo_user_id,
                    _blank_to_none(vps_barcode),
                    _blank_to_none(vps_barcode),
                    _blank_to_none(email),
                    _blank_to_none(email),
                ),
            ).fetchone()
            if user and not user["active"]:
                raise RuntimeError("vps_user_disabled")
            if user:
                return conn.execute(
                    """
                    UPDATE sales_users
                    SET odoo_user_id = COALESCE(odoo_user_id, %s),
                        vps_barcode = COALESCE(vps_barcode, %s),
                        department = COALESCE(%s, department),
                        reply_to_email = COALESCE(reply_to_email, %s),
                        auth_provider = 'vps'
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        odoo_user_id,
                        _blank_to_none(vps_barcode),
                        _blank_to_none(department),
                        _clean_optional_email(email),
                        user["id"],
                    ),
                ).fetchone()
            if not auto_create:
                raise RuntimeError("vps_user_not_mapped")
            return conn.execute(
                """
                INSERT INTO sales_users(
                    username, password_hash, display_name, role, daily_source_limit, daily_send_limit,
                    reply_to_email, must_change_password, password_changed_at,
                    odoo_user_id, vps_barcode, department, auth_provider
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, NOW(), %s, %s, %s, 'vps')
                RETURNING *
                """,
                (
                    login_name,
                    hash_password(secrets.token_urlsafe(32)),
                    display_name or login_name,
                    role,
                    daily_source_limit,
                    daily_send_limit,
                    _clean_optional_email(email),
                    odoo_user_id,
                    _blank_to_none(vps_barcode),
                    _blank_to_none(department),
                ),
            ).fetchone()

    def pdca_login_user(
        self,
        *,
        subject: str,
        username: str,
        display_name: str,
        pdca_role: str,
        data_scope: str,
        owner_key: str | None = None,
        team_key: str | None = None,
        owner_keys: list[str] | tuple[str, ...] = (),
        daily_source_limit: int = 100,
        daily_send_limit: int = 200,
    ) -> dict[str, Any]:
        """Map a verified PDCA identity to an existing least-privilege account."""
        login_name = str(username or "").strip()
        mapped_role = "admin" if pdca_role == "admin" and data_scope == "all" else "sales"
        with self.db.connect() as conn:
            user = conn.execute(
                """
                SELECT * FROM sales_users
                WHERE pdca_subject = %s OR LOWER(username) = LOWER(%s)
                ORDER BY CASE WHEN pdca_subject = %s THEN 0 ELSE 1 END, id
                LIMIT 1
                """,
                (subject, login_name, subject),
            ).fetchone()
            if user and not user["active"]:
                raise RuntimeError("pdca_user_disabled")
            values = (
                display_name or login_name,
                mapped_role,
                subject,
                pdca_role,
                data_scope,
                _blank_to_none(owner_key),
                _blank_to_none(team_key),
                json.dumps(list(owner_keys), ensure_ascii=False),
            )
            if user:
                return conn.execute(
                    """
                    UPDATE sales_users
                    SET display_name = %s,
                        role = %s,
                        pdca_subject = %s,
                        pdca_role = %s,
                        pdca_data_scope = %s,
                        pdca_owner_key = %s,
                        pdca_team_key = %s,
                        pdca_owner_keys = %s::jsonb,
                        auth_provider = 'pdca'
                    WHERE id = %s
                    RETURNING *
                    """,
                    (*values, user["id"]),
                ).fetchone()
            return conn.execute(
                """
                INSERT INTO sales_users(
                    username, password_hash, display_name, role,
                    daily_source_limit, daily_send_limit,
                    must_change_password, password_changed_at, auth_provider,
                    pdca_subject, pdca_role, pdca_data_scope, pdca_owner_key,
                    pdca_team_key, pdca_owner_keys
                )
                VALUES (%s, %s, %s, %s, %s, %s, FALSE, NOW(), 'pdca',
                        %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    login_name,
                    hash_password(secrets.token_urlsafe(32)),
                    display_name or login_name,
                    mapped_role,
                    daily_source_limit,
                    daily_send_limit,
                    subject,
                    pdca_role,
                    data_scope,
                    _blank_to_none(owner_key),
                    _blank_to_none(team_key),
                    json.dumps(list(owner_keys), ensure_ascii=False),
                ),
            ).fetchone()

    def list_users(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT u.id, u.username, u.display_name, u.role, u.daily_source_limit, u.daily_send_limit,
                       u.reply_to_email, u.sender_alias_localpart,
                       u.active, u.must_change_password, u.created_at,
                       COALESCE(usage.source_count, 0) AS source_count_today,
                       COALESCE(usage.send_count, 0) AS send_count_today
                FROM sales_users u
                LEFT JOIN user_daily_usage usage
                  ON usage.user_id = u.id AND usage.usage_date = CURRENT_DATE
                ORDER BY u.id
                """
            ).fetchall()

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT id, username, display_name, role, daily_source_limit, daily_send_limit,
                       reply_to_email, sender_alias_localpart, active, must_change_password, created_at
                FROM sales_users
                WHERE id = %s
                """,
                (user_id,),
            ).fetchone()

    def update_user(
        self,
        user_id: int,
        *,
        display_name: str | None = None,
        role: str | None = None,
        daily_source_limit: int | None = None,
        daily_send_limit: int | None = None,
        reply_to_email: str | None = None,
        sender_alias_localpart: str | None = None,
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
                    reply_to_email = COALESCE(%s, reply_to_email),
                    sender_alias_localpart = COALESCE(%s, sender_alias_localpart),
                    active = COALESCE(%s, active)
                WHERE id = %s
                RETURNING id, username, display_name, role, daily_source_limit, daily_send_limit,
                          reply_to_email, sender_alias_localpart, active, must_change_password, created_at
                """,
                (
                    display_name,
                    role,
                    daily_source_limit,
                    daily_send_limit,
                    _clean_optional_email(reply_to_email),
                    _clean_optional_alias(sender_alias_localpart),
                    active,
                    user_id,
                ),
            ).fetchone()

    def reset_user_password(self, user_id: int, password: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            user = conn.execute(
                """
                UPDATE sales_users
                SET password_hash = %s,
                    must_change_password = TRUE,
                    password_changed_at = NULL
                WHERE id = %s
                RETURNING id, username, display_name, role, daily_source_limit, daily_send_limit,
                          reply_to_email, active, must_change_password, created_at
                """,
                (hash_password(password), user_id),
            ).fetchone()
            if user:
                conn.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))
            return user

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
                          reply_to_email, active, must_change_password, created_at
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

    def record_audit_log(
        self,
        *,
        user: dict[str, Any] | None,
        action: str,
        target_type: str | None = None,
        target_id: str | int | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        safe_metadata = metadata or {}
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs(
                  user_id, username, display_name, role, action, target_type, target_id,
                  summary, metadata, ip_address, user_agent, success, error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                """,
                (
                    user.get("id") if user else None,
                    user.get("username") if user else None,
                    user.get("display_name") if user else None,
                    user.get("role") if user else None,
                    action,
                    target_type,
                    str(target_id) if target_id is not None else None,
                    summary,
                    json.dumps(safe_metadata, ensure_ascii=False, default=str),
                    ip_address,
                    user_agent,
                    success,
                    error,
                ),
            )

    def list_audit_logs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT id, user_id, username, display_name, role, action, target_type, target_id,
                       summary, metadata, ip_address, success, error, created_at
                FROM audit_logs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (max(1, min(int(limit or 100), 500)),),
            ).fetchall()

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
            rows = conn.execute(
                f"""
                UPDATE global_daily_usage
                SET {field} = {field} + %s
                WHERE usage_date = CURRENT_DATE
                RETURNING usage_date, source_count, send_count
                """,
                (amount,),
            ).fetchone()
            return rows

    def ensure_sender_account(self, account: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            name = account.get("name") or account.get("email")
            email = account.get("email")
            by_email = conn.execute("SELECT id FROM sender_accounts WHERE email = %s", (email,)).fetchone()
            by_name = conn.execute("SELECT id FROM sender_accounts WHERE name = %s", (name,)).fetchone()
            existing = by_email or by_name
            if existing:
                # Older deployments may already contain the same logical sender under
                # its display name but with a previous address. Update that row instead
                # of relying on a single unique constraint as the conflict target.
                safe_name = name if not by_name or int(by_name["id"]) == int(existing["id"]) else None
                safe_email = email if not by_email or int(by_email["id"]) == int(existing["id"]) else None
                return conn.execute(
                    """
                    UPDATE sender_accounts
                    SET name = COALESCE(%s, name),
                        email = COALESCE(%s, email),
                        provider = %s,
                        daily_limit = %s,
                        warmup_stage = %s
                    WHERE id = %s
                    RETURNING id, name, email, provider, daily_limit, warmup_stage, active, created_at
                    """,
                    (
                        safe_name,
                        safe_email,
                        account.get("provider", "resend"),
                        int(account.get("daily_limit") or 100),
                        account.get("warmup_stage", "production"),
                        int(existing["id"]),
                    ),
                ).fetchone()
            return conn.execute(
                """
                INSERT INTO sender_accounts(name, email, provider, daily_limit, warmup_stage, active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                RETURNING id, name, email, provider, daily_limit, warmup_stage, active, created_at
                """,
                (
                    name,
                    email,
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

    def upsert_contacts(
        self,
        contacts: Iterable[dict[str, Any]],
        *,
        owner_user_id: int | None = None,
        pool_type: str | None = None,
    ) -> tuple[int, int]:
        inserted = skipped = 0
        sql = """
        INSERT INTO contacts (
          linkedin_url, first_name, last_name, email, email_status, job_title, company_name,
          company_domain, industry, location, company_size, status, source_person_id, source, owner_user_id, owner,
          email_candidates, lead_score, search_task_id, phone, phone_candidates, source_context,
          identity_confidence, identity_status, identity_evidence,
          pool_type, assignment_source, assigned_at, pool_expires_at, last_stage_changed_at
        ) VALUES (
          %(linkedin_url)s, %(first_name)s, %(last_name)s, %(email)s, %(email_status)s, %(job_title)s, %(company_name)s,
          %(company_domain)s, %(industry)s, %(location)s, %(company_size)s, %(status)s, %(source_person_id)s, %(source)s,
          %(owner_user_id)s, %(owner)s, %(email_candidates)s::jsonb, %(lead_score)s, %(search_task_id)s,
          %(phone)s, %(phone_candidates)s::jsonb, %(source_context)s::jsonb,
          %(identity_confidence)s, %(identity_status)s, %(identity_evidence)s::jsonb,
          %(pool_type)s, %(assignment_source)s,
          CASE WHEN %(owner_user_id)s::bigint IS NULL THEN NULL ELSE NOW() END,
          CASE WHEN %(owner_user_id)s::bigint IS NULL THEN NULL ELSE NOW() + INTERVAL '60 days' END,
          NOW()
        )
        ON CONFLICT (linkedin_url) DO UPDATE
        SET source_person_id = COALESCE(EXCLUDED.source_person_id, contacts.source_person_id),
            owner_user_id = CASE
                WHEN contacts.pool_type = 'public' THEN NULL
                ELSE COALESCE(contacts.owner_user_id, EXCLUDED.owner_user_id)
            END,
            owner = CASE
                WHEN contacts.pool_type = 'public' THEN NULL
                ELSE COALESCE(contacts.owner, EXCLUDED.owner)
            END,
            pool_type = contacts.pool_type,
            lead_score = COALESCE(EXCLUDED.lead_score, contacts.lead_score),
            identity_confidence = COALESCE(EXCLUDED.identity_confidence, contacts.identity_confidence),
            identity_status = COALESCE(EXCLUDED.identity_status, contacts.identity_status),
            identity_evidence = CASE
                WHEN EXCLUDED.identity_evidence <> '[]'::jsonb THEN EXCLUDED.identity_evidence
                ELSE contacts.identity_evidence
            END,
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
            source_context = CASE
                WHEN EXCLUDED.source_context <> '{}'::jsonb THEN contacts.source_context || EXCLUDED.source_context
                ELSE contacts.source_context
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
           OR (contacts.source_context = '{}'::jsonb AND EXCLUDED.source_context <> '{}'::jsonb)
        RETURNING (xmax = 0) AS inserted
        """
        with self.db.connect() as conn:
            for contact in contacts:
                defaults = _contact_defaults(contact)
                defaults["owner_user_id"] = owner_user_id or contact.get("owner_user_id")
                defaults["owner"] = contact.get("owner")
                defaults["pool_type"] = _normalize_pool_type(pool_type or contact.get("pool_type"), defaults["owner_user_id"])
                defaults["assignment_source"] = contact.get("assignment_source") or ("import_owner" if defaults["owner_user_id"] else "automated_sourcing")
                defaults["email_candidates"] = json.dumps(contact.get("email_candidates") or [])
                defaults["phone_candidates"] = json.dumps(contact.get("phone_candidates") or [])
                defaults["source_context"] = json.dumps(contact.get("source_context") or {}, ensure_ascii=False)
                defaults["lead_score"] = contact.get("lead_score")
                defaults["search_task_id"] = contact.get("search_task_id")
                defaults["identity_confidence"] = contact.get("identity_confidence")
                defaults["identity_status"] = contact.get("identity_status")
                defaults["identity_evidence"] = json.dumps(contact.get("identity_evidence") or [])
                row = conn.execute(sql, defaults).fetchone()
                if row and row["inserted"]:
                    inserted += 1
                else:
                    skipped += 1
        return inserted, skipped

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return _with_customer_intelligence(conn.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,)).fetchone())

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

    def find_contact_match(self, contact: dict[str, Any]) -> dict[str, Any] | None:
        """Find a canonical contact using stable identifiers before inserting a lead."""

        clauses: list[str] = []
        params: list[Any] = []
        linkedin_url = str(contact.get("linkedin_url") or "").strip()
        email = _clean_optional_email(contact.get("email"))
        phone = re.sub(r"\D", "", str(contact.get("phone") or ""))
        domain = str(contact.get("company_domain") or "").strip().lower()
        first = str(contact.get("first_name") or "").strip()
        last = str(contact.get("last_name") or "").strip()
        if linkedin_url:
            clauses.append("linkedin_url = %s")
            params.append(linkedin_url)
        if email:
            clauses.append("LOWER(email) = LOWER(%s)")
            params.append(email)
        if len(phone) >= 7:
            clauses.append("regexp_replace(COALESCE(phone, ''), '[^0-9]+', '', 'g') = %s")
            params.append(phone)
        if first and last and domain:
            clauses.append(
                "(LOWER(first_name) = LOWER(%s) AND LOWER(last_name) = LOWER(%s) "
                "AND LOWER(COALESCE(company_domain, '')) = LOWER(%s))"
            )
            params.extend([first, last, domain])
        if not clauses:
            return None
        with self.db.connect() as conn:
            return conn.execute(
                f"SELECT * FROM contacts WHERE {' OR '.join(clauses)} ORDER BY created_at DESC, id DESC LIMIT 1",
                tuple(params),
            ).fetchone()

    def get_contact_for_user(self, contact_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
        if user.get("role") == "admin":
            return self.get_contact(contact_id)
        with self.db.connect() as conn:
            return _with_customer_intelligence(conn.execute(
                "SELECT * FROM contacts WHERE id = %s AND (owner_user_id = %s OR pool_type = 'public')",
                (contact_id, user["id"]),
            ).fetchone())

    def get_private_contact_for_user(self, contact_id: int, user: dict[str, Any]) -> dict[str, Any] | None:
        if user.get("role") == "admin":
            return self.get_contact(contact_id)
        with self.db.connect() as conn:
            return _with_customer_intelligence(conn.execute(
                "SELECT * FROM contacts WHERE id = %s AND owner_user_id = %s AND pool_type = 'private'",
                (contact_id, user["id"]),
            ).fetchone())

    def list_for_enrichment(self, limit: int, *, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        owner_filter, owner_params = self._owner_filter("contacts", user, prefix="AND")
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT * FROM contacts
                WHERE (
                    status = 'new'
                    OR (status = 'enriched' AND (enriched_at IS NULL OR enriched_at < NOW() - INTERVAL '30 days'))
                  )
                  {owner_filter}
                ORDER BY created_at
                LIMIT %s
                """,
                tuple(owner_params + [limit]),
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
            sabcd = conn.execute(
                f"""
                SELECT sabcd_stage, COUNT(*) AS count
                FROM contacts c
                {owner_filter}
                GROUP BY sabcd_stage
                ORDER BY sabcd_stage
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
            "sabcd": {row["sabcd_stage"]: int(row["count"]) for row in sabcd},
            "dispositions": {row["disposition"]: int(row["count"]) for row in disposition},
        }

    def owner_import_report(self, *, user: dict[str, Any] | None = None) -> dict[str, Any]:
        tracked_owners = ("April", "Haiwen", "Viki", "Ivan", "Vivi")
        owner_scope = ""
        params: list[Any] = []
        if user and user.get("role") != "admin":
            owner_scope = "AND c.owner_user_id = %s"
            params.append(user["id"])
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                  c.owner,
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE c.email IS NOT NULL AND c.email <> '') AS with_email,
                  COUNT(*) FILTER (WHERE c.email IS NULL OR c.email = '') AS without_email,
                  COUNT(*) FILTER (WHERE c.status = 'sent_1') AS sent_step_1,
                  COUNT(*) FILTER (WHERE c.status = 'queued') AS queued,
                  COUNT(*) FILTER (WHERE c.status = 'enriched') AS enriched,
                  COUNT(*) FILTER (WHERE c.status = 'new') AS new_contacts,
                  COUNT(*) FILTER (WHERE c.status = 'bounced') AS bounced
                FROM contacts c
                WHERE c.owner IN ('April', 'Haiwen', 'Viki', 'Ivan', 'Vivi')
                  {owner_scope}
                GROUP BY c.owner
                ORDER BY c.owner
                """,
                tuple(params),
            ).fetchall()
            sent_rows = conn.execute(
                f"""
                SELECT
                  c.owner,
                  COUNT(*) AS sent_total,
                  MAX(e.occurred_at) AS last_sent_at
                FROM email_events e
                JOIN contacts c ON c.id = e.contact_id
                WHERE e.event_type = 'sent'
                  AND c.owner IN ('April', 'Haiwen', 'Viki', 'Ivan', 'Vivi')
                  {owner_scope}
                GROUP BY c.owner
                """,
                tuple(params),
            ).fetchall()
        sent_by_owner = {row["owner"]: row for row in sent_rows}
        owners = []
        for owner in tracked_owners:
            base = next((row for row in rows if row["owner"] == owner), None)
            sent = sent_by_owner.get(owner, {})
            owners.append(
                {
                    "owner": owner,
                    "total": int(base["total"]) if base else 0,
                    "with_email": int(base["with_email"]) if base else 0,
                    "without_email": int(base["without_email"]) if base else 0,
                    "sent_step_1": int(base["sent_step_1"]) if base else 0,
                    "queued": int(base["queued"]) if base else 0,
                    "enriched": int(base["enriched"]) if base else 0,
                    "new": int(base["new_contacts"]) if base else 0,
                    "bounced": int(base["bounced"]) if base else 0,
                    "sent_total": int(sent.get("sent_total") or 0),
                    "last_sent_at": sent.get("last_sent_at"),
                }
            )
        return {
            "scope": "team" if not user or user.get("role") == "admin" else "mine",
            "owners": [item for item in owners if item["total"] or (user and user.get("role") == "admin")],
            "totals": {
                "total": sum(item["total"] for item in owners),
                "with_email": sum(item["with_email"] for item in owners),
                "sent_total": sum(item["sent_total"] for item in owners),
                "queued": sum(item["queued"] for item in owners),
            },
        }

    def company_seed_batch_report(self, task_ids: list[int], *, user: dict[str, Any] | None = None) -> dict[str, Any]:
        task_ids = [int(task_id) for task_id in task_ids if task_id]
        if not task_ids:
            return {"sendable": [], "review": [], "summary": {"contacts": 0, "sendable": 0, "review": 0}}
        placeholders = ", ".join(["%s"] * len(task_ids))
        params: list[Any] = [*task_ids]
        owner_clause = ""
        if user and user.get("role") != "admin":
            owner_clause = "AND c.owner_user_id = %s"
            params.append(user["id"])
        with self.db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT c.id, c.first_name, c.last_name, c.email, c.email_status, c.job_title,
                       c.company_name, c.company_domain, c.status::text, c.email_source,
                       c.email_confidence, c.email_candidates, c.phone, c.phone_candidates,
                       c.search_task_id, c.owner
                FROM contacts c
                WHERE c.search_task_id IN ({placeholders})
                  {owner_clause}
                ORDER BY c.company_name, c.id
                """,
                tuple(params),
            ).fetchall()
        sendable_by_email: dict[str, dict[str, Any]] = {}
        review_by_email: dict[str, dict[str, Any]] = {}
        for row in rows:
            full_name = " ".join([str(row.get("first_name") or "").strip(), str(row.get("last_name") or "").strip()]).strip()
            base = {
                "contact_id": row.get("id"),
                "name": full_name,
                "job_title": row.get("job_title") or "",
                "company_name": row.get("company_name") or "",
                "company_domain": row.get("company_domain") or "",
                "status": row.get("status") or "",
                "owner": row.get("owner") or "",
                "phone": row.get("phone") or "",
                "search_task_id": row.get("search_task_id"),
            }
            email = str(row.get("email") or "").strip().lower()
            if email and (row.get("email_status") == "valid" or row.get("status") in {"enriched", "queued", "sent_1", "sent_2", "sent_3"}):
                sendable_by_email.setdefault(
                    email,
                    {
                        **base,
                        "email": email,
                        "email_status": row.get("email_status") or "",
                        "email_source": row.get("email_source") or "",
                        "email_confidence": row.get("email_confidence") or "",
                        "contact_ids": [],
                    },
                )["contact_ids"].append(row.get("id"))
            candidates = row.get("email_candidates") or []
            if isinstance(candidates, str):
                try:
                    candidates = json.loads(candidates)
                except Exception:
                    candidates = []
            for candidate in candidates if isinstance(candidates, list) else []:
                candidate_email = str(candidate.get("email") or "").strip().lower()
                if not candidate_email or candidate_email in sendable_by_email:
                    continue
                category = candidate.get("category") or ""
                candidate_status = candidate.get("status") or "candidate"
                if category == "company_generic":
                    risk = "公司通用邮箱，只能人工复核，不建议自动群发"
                elif candidate_status in {"accept_all", "unknown", "unverified", "candidate"}:
                    risk = "未验证或 accept_all，存在退信风险"
                else:
                    risk = "候选邮箱，需人工确认"
                review_by_email.setdefault(
                    candidate_email,
                    {
                        **base,
                        "email": candidate_email,
                        "candidate_status": candidate_status,
                        "category": category,
                        "source": candidate.get("source") or "",
                        "confidence": candidate.get("confidence") or "",
                        "risk": risk,
                        "contact_ids": [],
                    },
                )["contact_ids"].append(row.get("id"))
        return {
            "summary": {
                "tasks": len(task_ids),
                "contacts": len(rows),
                "sendable": len(sendable_by_email),
                "review": len(review_by_email),
            },
            "sendable": list(sendable_by_email.values()),
            "review": list(review_by_email.values()),
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
            clauses.append("(c.owner_user_id = %s OR c.pool_type = 'public')")
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
            rows = conn.execute(
                f"""
                SELECT c.id, c.linkedin_url, c.first_name, c.last_name, c.email, c.email_status, c.job_title,
                       c.company_name, c.company_domain, c.industry, c.location, c.status::text,
                       c.sequence_step, c.last_contacted_at, c.replied_at, c.enriched_at,
                       c.enrich_error, c.notes, c.created_at, c.source_person_id, c.source,
                       c.email_source, c.email_confidence, c.email_candidates, c.phone, c.phone_candidates,
                       c.source_context,
                       c.social_profiles, c.social_enriched_at, c.social_error,
                       c.outreach_stage, c.lifecycle_stage, c.disposition, c.next_action_at,
                       c.sabcd_stage,
                       c.pool_type, c.assignment_source, c.assigned_at, c.pool_expires_at,
                       c.last_stage_changed_at, c.returned_to_public_at, c.claim_count,
                       c.reply_assignment_pending, c.last_reply_at,
                       c.owner, c.owner_user_id, c.lost_reason, c.profile_summary, c.profile_insights, c.profile_updated_at,
                       c.lead_score, c.search_task_id,
                       c.identity_confidence, c.identity_status, c.identity_evidence,
                       draft.id AS draft_id,
                       draft.status AS draft_status,
                       draft.subject AS draft_subject,
                       draft.sequence_step AS draft_sequence_step,
                       draft.created_at AS draft_created_at,
                       draft.approved_at AS draft_approved_at,
                       COALESCE(ev.sent_count, 0) AS sent_count,
                       COALESCE(ev.delivered_count, 0) AS delivered_count,
                       COALESCE(ev.opened_count, 0) AS opened_count,
                       COALESCE(ev.clicked_count, 0) AS clicked_count,
                       COALESCE(ev.replied_count, 0) AS replied_count,
                       COALESCE(ev.bounced_count, 0) AS bounced_count,
                       COALESCE(ev.unsubscribed_count, 0) AS unsubscribed_count,
                       ev.last_event_at,
                       ev.last_event_type
                FROM contacts c
                LEFT JOIN LATERAL (
                    SELECT d.id, d.status, d.subject, d.sequence_step, d.created_at, d.approved_at
                    FROM email_drafts d
                    WHERE d.contact_id = c.id
                      AND d.user_id = c.owner_user_id
                    ORDER BY d.created_at DESC, d.id DESC
                    LIMIT 1
                ) draft ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) FILTER (WHERE event_type = 'sent') AS sent_count,
                        COUNT(*) FILTER (WHERE event_type = 'delivered') AS delivered_count,
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
        return [_with_customer_intelligence(row) for row in rows]

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
            "public_pool": "c.pool_type = 'public'",
            "private_pool": "c.pool_type = 'private'",
            "my_private_pool": "c.pool_type = 'private' AND c.owner_user_id IS NOT NULL",
            "pool_expiring": "c.pool_type = 'private' AND c.sabcd_stage <> 'S' AND c.pool_expires_at <= NOW() + INTERVAL '14 days'",
            "returned_pool": "c.pool_type = 'public' AND c.returned_to_public_at IS NOT NULL",
            "unassigned_replies": "c.pool_type = 'public' AND c.reply_assignment_pending = TRUE",
            "needs_enrichment": "(c.email_status IS DISTINCT FROM 'valid' OR c.email IS NULL)",
            "auto_enrich": "(c.email_status IS DISTINCT FROM 'valid' OR c.email IS NULL) AND COALESCE(jsonb_array_length(c.email_candidates), 0) = 0 AND COALESCE(c.identity_status, '') IS DISTINCT FROM 'mismatch'",
            "needs_review": "(COALESCE(c.identity_status, '') = 'mismatch' OR (COALESCE(c.identity_status, '') = 'review' AND COALESCE(c.identity_confidence, c.lead_score, 0) < 70) OR ((c.email_status IS DISTINCT FROM 'valid' OR c.email IS NULL) AND COALESCE(jsonb_array_length(c.email_candidates), 0) > 0) OR c.enrich_error IS NOT NULL)",
            "ready_to_send": "c.email_status = 'valid' AND c.status = 'enriched' AND c.email IS NOT NULL AND lower(split_part(c.email, '@', 1)) NOT IN ('admin','billing','contact','hello','help','info','office','press','sales','support','team') AND COALESCE(c.lead_score, 60) >= 50 AND COALESCE(c.job_title, '') !~* '(assistant|customer service|intern|reception|receptionist|support)'",
            "missing_draft": "c.pool_type = 'private' AND c.email_status = 'valid' AND c.email IS NOT NULL AND draft.id IS NULL",
            "draft_pending": "c.pool_type = 'private' AND draft.status = 'draft'",
            "draft_approved": "c.pool_type = 'private' AND draft.status = 'approved'",
            "opened_no_reply": "COALESCE(ev.opened_count, 0) > 0 AND c.status NOT IN ('replied', 'bounced', 'unsubscribed')",
            "replied": "(c.status = 'replied' OR COALESCE(ev.replied_count, 0) > 0)",
            "bounced": "(c.status = 'bounced' OR COALESCE(ev.bounced_count, 0) > 0)",
            "second_touch_due": "c.status = 'sent_1'",
            "third_touch_due": "c.status = 'sent_2'",
            "waiting_pool": "c.lifecycle_stage = 'waiting_pool'",
            "abandoned": "(c.lifecycle_stage = 'abandoned' OR c.disposition = 'abandoned')",
            "sabcd_d": "c.pool_type = 'private' AND c.sabcd_stage = 'D'",
            "sabcd_c": "c.pool_type = 'private' AND c.sabcd_stage = 'C'",
            "sabcd_b": "c.pool_type = 'private' AND c.sabcd_stage = 'B'",
            "sabcd_a": "c.pool_type = 'private' AND c.sabcd_stage = 'A'",
            "sabcd_s": "c.pool_type = 'private' AND c.sabcd_stage = 'S'",
        }
        clause = filters.get(filter_key)
        if clause:
            clauses.append(clause)

    def list_sent_emails(
        self,
        *,
        user: dict[str, Any] | None = None,
        limit: int = 100,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        owner_filter, owner_params = self._owner_filter("c", user, prefix="AND")
        clauses = ["e.event_type = 'sent'"]
        params: list[Any] = []
        if search:
            clauses.append(
                """
                (
                  c.email ILIKE %s
                  OR c.company_name ILIKE %s
                  OR c.first_name ILIKE %s
                  OR c.last_name ILIKE %s
                  OR e.email_subject ILIKE %s
                  OR COALESCE(e.metadata->>'sender_email', '') ILIKE %s
                )
                """
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like, like])
        where = "WHERE " + " AND ".join(clauses)
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT e.id,
                       e.contact_id,
                       e.sequence_step,
                       e.email_subject,
                       e.message_id,
                       e.occurred_at,
                       e.metadata,
                       COALESCE(e.metadata->>'sender_email', '') AS sender_email,
                       COALESCE(e.metadata->>'sender_id', '') AS sender_id,
                       COALESCE(e.metadata->>'reply_to_email', '') AS reply_to_email,
                       COALESCE(e.metadata->>'mode', '') AS mode,
                       COALESCE(NULLIF(e.metadata->>'dry_run', '')::boolean, FALSE) AS dry_run,
                       c.first_name,
                       c.last_name,
                       c.email AS recipient_email,
                       c.job_title,
                       c.company_name,
                       c.company_domain,
                       c.status,
                       c.sabcd_stage,
                       COALESCE(ev.delivered_count, 0) AS delivered_count,
                       COALESCE(ev.opened_count, 0) AS opened_count,
                       COALESCE(ev.replied_count, 0) AS replied_count,
                       COALESCE(ev.bounced_count, 0) AS bounced_count,
                       COALESCE(ev.complained_count, 0) AS complained_count,
                       COALESCE(ev.last_feedback_at, e.occurred_at) AS last_feedback_at,
                       ev.last_feedback_type
                FROM email_events e
                JOIN contacts c ON c.id = e.contact_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) FILTER (WHERE event_type = 'delivered') AS delivered_count,
                           COUNT(*) FILTER (WHERE event_type = 'opened') AS opened_count,
                           COUNT(*) FILTER (WHERE event_type = 'replied') AS replied_count,
                           COUNT(*) FILTER (WHERE event_type = 'bounced') AS bounced_count,
                           COUNT(*) FILTER (WHERE event_type = 'complained') AS complained_count,
                           MAX(occurred_at) FILTER (WHERE event_type <> 'sent') AS last_feedback_at,
                           (ARRAY_AGG(event_type::text ORDER BY occurred_at DESC) FILTER (WHERE event_type <> 'sent'))[1] AS last_feedback_type
                    FROM email_events
                    WHERE contact_id = e.contact_id
                      AND sequence_step = e.sequence_step
                ) ev ON TRUE
                {where}
                  {owner_filter}
                ORDER BY e.occurred_at DESC, e.id DESC
                LIMIT %s
                """,
                tuple(params + owner_params + [max(1, min(int(limit), 500))]),
            ).fetchall()

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
                  COUNT(*) FILTER (WHERE e.event_type = 'delivered') AS delivered_today,
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
                       u.reply_to_email, u.daily_source_limit, u.daily_send_limit,
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
                  SELECT COALESCE(NULLIF(enrich_error, ''), '邮箱富化无结果') AS reason
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
            funnel = conn.execute(
                f"""
                SELECT
                  COUNT(*) AS leads,
                  COUNT(*) FILTER (WHERE c.pool_type = 'public') AS public_pool,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private') AS private_pool,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND c.email_status = 'valid' AND c.email IS NOT NULL) AS valid_email,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND c.profile_insights <> '{{}}'::jsonb) AS profiled,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND draft.id IS NOT NULL) AS drafted,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND draft.status IN ('approved', 'sent')) AS approved,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND ev.sent > 0) AS sent,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND ev.opened > 0) AS opened,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND ev.replied > 0) AS replied,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND c.sabcd_stage IN ('B', 'A', 'S')) AS qualified,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND c.sabcd_stage = 'S') AS signed
                FROM contacts c
                LEFT JOIN LATERAL (
                  SELECT id, status FROM email_drafts d
                  WHERE d.contact_id = c.id
                    AND d.user_id = c.owner_user_id
                  ORDER BY d.created_at DESC, d.id DESC
                  LIMIT 1
                ) draft ON TRUE
                LEFT JOIN LATERAL (
                  SELECT
                    COUNT(*) FILTER (WHERE e.event_type = 'sent') AS sent,
                    COUNT(*) FILTER (WHERE e.event_type = 'opened') AS opened,
                    COUNT(*) FILTER (WHERE e.event_type = 'replied') AS replied
                  FROM email_events e WHERE e.contact_id = c.id
                ) ev ON TRUE
                WHERE TRUE {owner_filter}
                """,
                tuple(owner_params),
            ).fetchone()
            blockers = conn.execute(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE c.pool_type = 'public') AS public_unassigned,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND (c.email_status <> 'valid' OR c.email IS NULL)) AS missing_email,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND c.profile_insights = '{{}}'::jsonb) AS missing_profile,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND c.email_status = 'valid' AND draft.id IS NULL) AS missing_draft,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND draft.status = 'draft') AS awaiting_approval,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND draft.status = 'approved' AND ev.sent = 0) AS approved_not_sent,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND ev.opened > 0 AND ev.replied = 0 AND c.status NOT IN ('bounced', 'unsubscribed')) AS opened_no_reply,
                  COUNT(*) FILTER (WHERE c.pool_type = 'private' AND c.status = 'bounced') AS bounced
                FROM contacts c
                LEFT JOIN LATERAL (
                  SELECT id, status FROM email_drafts d
                  WHERE d.contact_id = c.id
                    AND d.user_id = c.owner_user_id
                  ORDER BY d.created_at DESC, d.id DESC
                  LIMIT 1
                ) draft ON TRUE
                LEFT JOIN LATERAL (
                  SELECT
                    COUNT(*) FILTER (WHERE e.event_type = 'sent') AS sent,
                    COUNT(*) FILTER (WHERE e.event_type = 'opened') AS opened,
                    COUNT(*) FILTER (WHERE e.event_type = 'replied') AS replied
                  FROM email_events e WHERE e.contact_id = c.id
                ) ev ON TRUE
                WHERE TRUE {owner_filter}
                """,
                tuple(owner_params),
            ).fetchone()
        return {
            "totals": {key: int(value or 0) for key, value in dict(totals).items()},
            "events": {key: int(value or 0) for key, value in dict(events).items()},
            "by_user": by_user,
            "provider_stats": provider_stats,
            "failures": failures,
            "funnel": {key: int(value or 0) for key, value in dict(funnel).items()},
            "blockers": {key: int(value or 0) for key, value in dict(blockers).items()},
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
        created_by_user_id: int | None,
        owner_user_id: int | None,
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

    def get_active_user(self, user_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM sales_users WHERE id = %s AND active = TRUE", (user_id,)).fetchone()

    def list_due_acquisition_plans(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM acquisition_plans
                WHERE status = 'active' AND next_run_at <= NOW()
                ORDER BY next_run_at, id
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def list_acquisition_plans(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT p.*, u.username, u.display_name,
                       latest.status AS last_run_status,
                       latest.metrics AS last_run_metrics,
                       latest.completed_at AS last_run_completed_at
                FROM acquisition_plans p
                LEFT JOIN sales_users u ON u.id = p.owner_user_id
                LEFT JOIN LATERAL (
                  SELECT status, metrics, completed_at
                  FROM acquisition_plan_runs
                  WHERE plan_id = p.id
                  ORDER BY run_date DESC, id DESC
                  LIMIT 1
                ) latest ON TRUE
                ORDER BY p.created_at DESC
                LIMIT %s
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()

    def create_acquisition_plan(
        self,
        *,
        name: str,
        regions: list[str],
        industries: list[str],
        company_types: list[str],
        role_terms: list[str],
        owner_user_id: int | None,
        pool_type: str = "private",
        daily_lead_limit: int,
        combinations_per_run: int,
    ) -> dict[str, Any]:
        if pool_type not in {"private", "public"}:
            raise ValueError("Invalid acquisition plan pool type")
        if pool_type == "private" and not owner_user_id:
            raise ValueError("Private acquisition plans require an owner")
        if pool_type == "public":
            owner_user_id = None
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO acquisition_plans(
                  name, regions, industries, company_types, role_terms,
                  owner_user_id, pool_type, daily_lead_limit, combinations_per_run
                )
                VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    name,
                    json.dumps(regions, ensure_ascii=False),
                    json.dumps(industries, ensure_ascii=False),
                    json.dumps(company_types, ensure_ascii=False),
                    json.dumps(role_terms, ensure_ascii=False),
                    owner_user_id,
                    pool_type,
                    daily_lead_limit,
                    combinations_per_run,
                ),
            ).fetchone()

    def begin_acquisition_plan_run(
        self,
        plan_id: int,
        combinations: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO acquisition_plan_runs(plan_id, combinations)
                VALUES (%s, %s::jsonb)
                ON CONFLICT(plan_id, run_date) DO UPDATE
                SET status = 'running', combinations = EXCLUDED.combinations,
                    metrics = '{}'::jsonb, error = NULL, started_at = NOW(), completed_at = NULL
                WHERE acquisition_plan_runs.status = 'failed'
                RETURNING *
                """,
                (plan_id, json.dumps(combinations, ensure_ascii=False)),
            ).fetchone()

    def finish_acquisition_plan_run(
        self,
        run_id: int,
        *,
        plan_id: int,
        status: str,
        metrics: dict[str, Any],
        cursor_advance: int,
        error: str | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Invalid acquisition plan run status")
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE acquisition_plan_runs
                SET status = %s, metrics = %s::jsonb, error = %s, completed_at = NOW()
                WHERE id = %s AND plan_id = %s
                """,
                (status, json.dumps(metrics, ensure_ascii=False), error, run_id, plan_id),
            )
            conn.execute(
                """
                UPDATE acquisition_plans
                SET cursor_position = cursor_position + %s,
                    next_run_at = CASE WHEN %s = 'completed' THEN NOW() + INTERVAL '1 day' ELSE NOW() + INTERVAL '1 hour' END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (max(0, int(cursor_advance)), status, plan_id),
            )

    def create_automation_run(
        self,
        *,
        run_type: str,
        input_payload: dict[str, Any],
        progress_total: int,
        user: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO automation_runs(
                    run_type, input_payload, progress_total, created_by_user_id,
                    owner_user_id, idempotency_key
                )
                VALUES (%s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT(owner_user_id, idempotency_key) DO UPDATE
                SET updated_at = automation_runs.updated_at
                RETURNING *
                """,
                (
                    run_type,
                    json.dumps(input_payload, ensure_ascii=False),
                    progress_total,
                    user["id"],
                    user["id"],
                    idempotency_key,
                ),
            ).fetchone()

    def list_automation_runs(self, *, user: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
        where = "" if user.get("role") == "admin" else "WHERE r.owner_user_id = %s"
        params: list[Any] = [] if user.get("role") == "admin" else [user["id"]]
        params.append(limit)
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT r.*, u.username, u.display_name
                FROM automation_runs r
                LEFT JOIN sales_users u ON u.id = r.owner_user_id
                {where}
                ORDER BY r.created_at DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()

    def get_automation_run(self, run_id: int, *, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        clauses = ["r.id = %s"]
        params: list[Any] = [run_id]
        if user and user.get("role") != "admin":
            clauses.append("r.owner_user_id = %s")
            params.append(user["id"])
        with self.db.connect() as conn:
            return conn.execute(
                f"SELECT r.* FROM automation_runs r WHERE {' AND '.join(clauses)}",
                tuple(params),
            ).fetchone()

    def claim_automation_run(self, run_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE automation_runs
                SET status = 'running', pause_requested = FALSE,
                    started_at = COALESCE(started_at, NOW()), updated_at = NOW(), error = NULL
                WHERE id = %s AND status IN ('queued', 'retrying')
                RETURNING *
                """,
                (run_id,),
            ).fetchone()

    def update_automation_run_progress(
        self,
        run_id: int,
        *,
        progress_current: int,
        result: dict[str, Any],
        stage: str = "sourcing",
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE automation_runs
                SET progress_current = %s, result = %s::jsonb, stage = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (progress_current, json.dumps(result, ensure_ascii=False), stage, run_id),
            )

    def finish_automation_run(self, run_id: int, *, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        completed = status in {"awaiting_approval", "completed", "failed", "cancelled"}
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE automation_runs
                SET status = %s,
                    stage = CASE WHEN %s = 'awaiting_approval' THEN 'review' ELSE stage END,
                    result = COALESCE(%s::jsonb, result), error = %s,
                    pause_requested = FALSE, updated_at = NOW(),
                    completed_at = CASE WHEN %s THEN NOW() ELSE completed_at END
                WHERE id = %s
                """,
                (status, status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, completed, run_id),
            )

    def request_automation_pause(self, run_id: int, *, user: dict[str, Any]) -> dict[str, Any] | None:
        owner_clause = "" if user.get("role") == "admin" else "AND owner_user_id = %s"
        params: list[Any] = [run_id]
        if owner_clause:
            params.append(user["id"])
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                UPDATE automation_runs
                SET pause_requested = TRUE,
                    status = CASE WHEN status = 'queued' THEN 'paused' ELSE status END,
                    updated_at = NOW()
                WHERE id = %s {owner_clause} AND status IN ('queued', 'running')
                RETURNING *
                """,
                tuple(params),
            ).fetchone()

    def resume_automation_run(self, run_id: int, *, user: dict[str, Any]) -> dict[str, Any] | None:
        owner_clause = "" if user.get("role") == "admin" else "AND owner_user_id = %s"
        params: list[Any] = [run_id]
        if owner_clause:
            params.append(user["id"])
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                UPDATE automation_runs
                SET status = 'queued', pause_requested = FALSE, error = NULL,
                    completed_at = NULL, updated_at = NOW()
                WHERE id = %s {owner_clause} AND status IN ('paused', 'failed')
                RETURNING *
                """,
                tuple(params),
            ).fetchone()

    def recover_automation_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """Requeue work interrupted by a process restart and return runnable jobs."""
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE automation_runs
                SET status = 'queued', updated_at = NOW(),
                    error = COALESCE(error, 'worker_restarted')
                WHERE status = 'running'
                """
            )
            return conn.execute(
                """
                SELECT * FROM automation_runs
                WHERE status = 'queued'
                ORDER BY created_at
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def create_lead_search_result(self, task_id: int, parsed: dict[str, Any], *, status: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO lead_search_results(
                    task_id, raw_title, raw_snippet, raw_url, linkedin_url, first_name, last_name,
                    job_title, company_name, company_domain, location, lead_score, email_candidates,
                    match_confidence, match_status, match_evidence, status, failure_reason
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s)
                RETURNING id, task_id, raw_title, raw_snippet, raw_url, linkedin_url, first_name, last_name,
                          job_title, company_name, company_domain, location, lead_score, email_candidates,
                          match_confidence, match_status, match_evidence,
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
                    int(parsed.get("match_confidence") or parsed.get("lead_score") or 0),
                    parsed.get("match_status") or "review",
                    json.dumps(parsed.get("match_evidence") or []),
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
                       r.match_confidence, r.match_status, r.match_evidence,
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

    def reserve_email_provider_credits(self, provider: str, amount: int, daily_limit: int) -> bool:
        amount = max(0, int(amount))
        daily_limit = max(0, int(daily_limit))
        if amount == 0:
            return True
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO email_provider_stats(provider, stat_date, credits_used)
                SELECT %s, CURRENT_DATE, %s
                WHERE %s <= %s
                ON CONFLICT (provider, stat_date) DO UPDATE
                SET credits_used = email_provider_stats.credits_used + EXCLUDED.credits_used,
                    updated_at = NOW()
                WHERE email_provider_stats.credits_used + EXCLUDED.credits_used <= %s
                RETURNING credits_used
                """,
                (provider, amount, amount, daily_limit, daily_limit),
            ).fetchone()
            return row is not None and int(row["credits_used"] or 0) <= daily_limit

    def get_provider_lookup_cache(self, provider: str, operation: str, lookup_key: str) -> list[dict[str, Any]] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT response
                FROM provider_lookup_cache
                WHERE provider = %s AND operation = %s AND lookup_key = %s
                  AND expires_at > NOW()
                """,
                (provider, operation, lookup_key),
            ).fetchone()
            return row["response"] if row else None

    def store_provider_lookup_cache(
        self,
        provider: str,
        operation: str,
        lookup_key: str,
        status: str,
        response: list[dict[str, Any]],
        ttl_seconds: int,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_lookup_cache(
                    provider, operation, lookup_key, status, response, expires_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (provider, operation, lookup_key) DO UPDATE
                SET status = EXCLUDED.status,
                    response = EXCLUDED.response,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
                """,
                (provider, operation, lookup_key, status, json.dumps(response), max(1, int(ttl_seconds))),
            )

    def get_llm_gateway_cache(self, cache_key: str, owner_user_id: int | None) -> str | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT response
                FROM llm_gateway_cache
                WHERE cache_key = %s
                  AND owner_user_id IS NOT DISTINCT FROM %s
                  AND expires_at > NOW()
                """,
                (cache_key, owner_user_id),
            ).fetchone()
            return str(row["response"]) if row else None

    def store_llm_gateway_cache(
        self,
        cache_key: str,
        owner_user_id: int | None,
        provider: str,
        model: str,
        operation: str,
        response: str,
        ttl_seconds: int,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_gateway_cache(cache_key, owner_user_id, provider, model, operation, response, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (cache_key) DO UPDATE
                SET response = EXCLUDED.response,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = NOW()
                WHERE llm_gateway_cache.owner_user_id IS NOT DISTINCT FROM EXCLUDED.owner_user_id
                """,
                (cache_key, owner_user_id, provider, model, operation, response, max(1, int(ttl_seconds))),
            )

    def reserve_llm_gateway_budget(
        self,
        provider: str,
        model: str,
        input_chars: int,
        output_chars: int,
        daily_calls: int,
        daily_input_chars: int,
        daily_output_chars: int,
    ) -> bool:
        input_chars = max(0, int(input_chars))
        output_chars = max(0, int(output_chars))
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO llm_gateway_daily_usage(provider, model, usage_date, calls, input_chars, output_chars)
                SELECT %s, %s, CURRENT_DATE, 1, %s, %s
                WHERE 1 <= %s AND %s <= %s AND %s <= %s
                ON CONFLICT (provider, model, usage_date) DO UPDATE
                SET calls = llm_gateway_daily_usage.calls + 1,
                    input_chars = llm_gateway_daily_usage.input_chars + EXCLUDED.input_chars,
                    output_chars = llm_gateway_daily_usage.output_chars + EXCLUDED.output_chars
                WHERE llm_gateway_daily_usage.calls + 1 <= %s
                  AND llm_gateway_daily_usage.input_chars + EXCLUDED.input_chars <= %s
                  AND llm_gateway_daily_usage.output_chars + EXCLUDED.output_chars <= %s
                RETURNING calls
                """,
                (
                    provider, model, input_chars, output_chars,
                    max(0, int(daily_calls)), input_chars, max(0, int(daily_input_chars)), output_chars, max(0, int(daily_output_chars)),
                    max(0, int(daily_calls)), max(0, int(daily_input_chars)), max(0, int(daily_output_chars)),
                ),
            ).fetchone()
            return row is not None

    def get_contactout_account(self, account_id: int, *, owner_user_id: int | None = None) -> dict[str, Any] | None:
        clauses = ["id = %s"]
        params: list[Any] = [account_id]
        if owner_user_id is not None:
            clauses.append("assigned_user_id = %s")
            params.append(owner_user_id)
        with self.db.connect() as conn:
            return conn.execute(
                f"SELECT * FROM contactout_accounts WHERE {' AND '.join(clauses)}",
                tuple(params),
            ).fetchone()

    def list_contactout_accounts(self, *, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user and user.get("role") != "admin":
            clauses.append("assigned_user_id = %s")
            params.append(int(user["id"]))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT id, account_key, display_name, masked_identity,
                       assigned_user_id, status, daily_limit, cooldown_until,
                       authorized_at, last_used_at, created_at, updated_at
                FROM contactout_accounts
                {where}
                ORDER BY display_name, id
                """,
                tuple(params),
            ).fetchall()

    def contactout_usage_today(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT scope_key, reserved_units, used_units, denied_count, updated_at
                FROM provider_account_daily_usage
                WHERE provider = 'contactout'
                  AND usage_date = timezone('Asia/Shanghai', NOW())::date
                ORDER BY scope_key
                """
            ).fetchall()

    def llm_gateway_usage_today(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT provider, model, calls, input_chars, output_chars
                FROM llm_gateway_daily_usage
                WHERE usage_date = CURRENT_DATE
                ORDER BY provider, model
                """
            ).fetchall()

    def upsert_contactout_account(
        self,
        *,
        account_key: str,
        display_name: str,
        masked_identity: str,
        credential_ref: str,
        assigned_user_id: int | None,
        daily_limit: int,
        authorized_by_user_id: int | None,
        status: str = "active",
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            previous_account = conn.execute(
                """
                SELECT id, assigned_user_id, status
                FROM contactout_accounts
                WHERE account_key = %s
                FOR UPDATE
                """,
                (account_key,),
            ).fetchone()
            account = conn.execute(
                """
                INSERT INTO contactout_accounts(
                    account_key, display_name, masked_identity, credential_ref,
                    assigned_user_id, daily_limit, authorized_by_user_id, authorized_at, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (account_key) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    masked_identity = EXCLUDED.masked_identity,
                    credential_ref = EXCLUDED.credential_ref,
                    assigned_user_id = EXCLUDED.assigned_user_id,
                    daily_limit = EXCLUDED.daily_limit,
                    authorized_by_user_id = EXCLUDED.authorized_by_user_id,
                    authorized_at = NOW(),
                    status = EXCLUDED.status,
                    cooldown_until = CASE WHEN EXCLUDED.status = 'active' THEN NULL ELSE contactout_accounts.cooldown_until END,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    account_key, display_name, masked_identity, credential_ref,
                    assigned_user_id, max(1, int(daily_limit)), authorized_by_user_id, status,
                ),
            ).fetchone()
            needs_fence = bool(
                previous_account
                and (
                    previous_account["assigned_user_id"] != account["assigned_user_id"]
                    or account["status"] != "active"
                )
            )
        # Keep account and job locks in separate transactions. Workers lock jobs
        # before rechecking the account, so this avoids the inverse lock order.
        if needs_fence:
            self._fence_contactout_account_jobs(account["id"])
        if status == "active":
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'retry_wait', attempts = 0, error_code = NULL,
                        next_attempt_at = NOW(), updated_at = NOW()
                    FROM contacts, contactout_accounts
                    WHERE contactout_enrichment_jobs.account_id = %s
                      AND contactout_enrichment_jobs.status = 'blocked'
                      AND contactout_enrichment_jobs.error_code IN ('challenge_required', 'reauth_required')
                      AND contacts.id = contactout_enrichment_jobs.contact_id
                      AND contacts.pool_type = 'private'
                      AND contacts.owner_user_id = contactout_enrichment_jobs.owner_user_id
                      AND contactout_accounts.id = contactout_enrichment_jobs.account_id
                      AND contactout_accounts.status = 'active'
                      AND contactout_accounts.assigned_user_id = contactout_enrichment_jobs.owner_user_id
                    """,
                    (account["id"],),
                )
        return account

    def _fence_contactout_account_jobs(self, account_id: int) -> None:
        with self.db.connect() as conn:
            stale_jobs = conn.execute(
                """
                SELECT * FROM contactout_enrichment_jobs
                WHERE account_id = %s
                  AND status IN ('queued', 'running', 'retry_wait', 'blocked')
                FOR UPDATE
                """,
                (account_id,),
            ).fetchall()
            account = conn.execute(
                """
                SELECT status, assigned_user_id
                FROM contactout_accounts
                WHERE id = %s
                FOR UPDATE
                """,
                (account_id,),
            ).fetchone()
            if not account:
                return
            for job in stale_jobs:
                if account["status"] == "active" and job["owner_user_id"] == account["assigned_user_id"]:
                    continue
                self._settle_contactout_quota(conn, job, consumed=bool(job.get("quota_reserved")))
                error_code = "account_reassigned"
                if job["owner_user_id"] == account["assigned_user_id"]:
                    error_code = (
                        account["status"]
                        if account["status"] in {"challenge_required", "reauth_required"}
                        else "account_unavailable"
                    )
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'blocked', error_code = %s,
                        lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (error_code, job["id"]),
                )

    def enqueue_contactout_job(self, **fields: Any) -> dict[str, Any]:
        with self.db.connect() as conn:
            job = conn.execute(
                """
                INSERT INTO contactout_enrichment_jobs(
                    idempotency_key, contact_id, owner_user_id, account_id, operation, input_hash
                )
                VALUES (%(idempotency_key)s, %(contact_id)s, %(owner_user_id)s,
                        %(account_id)s, %(operation)s, %(input_hash)s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING *
                """,
                fields,
            ).fetchone()
            if not job:
                job = conn.execute(
                    """
                    SELECT * FROM contactout_enrichment_jobs
                    WHERE idempotency_key = %s AND owner_user_id = %s
                    """,
                    (fields["idempotency_key"], fields["owner_user_id"]),
                ).fetchone()
            if not job:
                raise ValueError("contactout_job_conflict")
            return job

    def list_contactout_jobs(self, *, user: dict[str, Any] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user and user.get("role") != "admin":
            clauses.append("job.owner_user_id = %s")
            params.append(int(user["id"]))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(500, int(limit))))
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT job.*, account.display_name AS account_name, account.masked_identity,
                       result.match_status, result.match_confidence, result.review_required
                FROM contactout_enrichment_jobs job
                JOIN contactout_accounts account ON account.id = job.account_id
                LEFT JOIN contactout_enrichment_results result ON result.job_id = job.id
                {where}
                ORDER BY job.created_at DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()

    def block_expired_contactout_jobs(self) -> int:
        with self.db.connect() as conn:
            expired = conn.execute(
                """
                SELECT * FROM contactout_enrichment_jobs
                WHERE status = 'running' AND lease_expires_at < NOW()
                FOR UPDATE
                """
            ).fetchall()
            for job in expired:
                self._settle_contactout_quota(conn, job, consumed=bool(job.get("quota_reserved")))
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'blocked', error_code = 'lease_expired_unknown_charge',
                        lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job["id"], job["lease_token"]),
                )
            return len(expired)

    def claim_contactout_job(self, *, lease_seconds: int = 300) -> dict[str, Any] | None:
        token = secrets.token_urlsafe(24)
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE contactout_enrichment_jobs
                SET status = 'running', lease_token = %s,
                    lease_expires_at = NOW() + (%s * INTERVAL '1 second'),
                    started_at = COALESCE(started_at, NOW()), updated_at = NOW(), error_code = NULL
                WHERE id = (
                  SELECT job.id FROM contactout_enrichment_jobs job
                  JOIN contactout_accounts account ON account.id = job.account_id
                  WHERE job.status IN ('queued', 'retry_wait')
                    AND job.next_attempt_at <= NOW() AND job.attempts < job.max_attempts
                    AND account.status = 'active'
                    AND (account.cooldown_until IS NULL OR account.cooldown_until <= NOW())
                  ORDER BY job.priority DESC, job.created_at
                  FOR UPDATE OF job SKIP LOCKED
                  LIMIT 1
                )
                RETURNING *
                """,
                (token, max(30, int(lease_seconds))),
            ).fetchone()

    def reserve_contactout_job_quota(self, job_id: int, lease_token: str, *, global_limit: int) -> str:
        with self.db.connect() as conn:
            job = conn.execute(
                """
                SELECT job.*, account.daily_limit, account.status AS account_status,
                       account.cooldown_until, account.assigned_user_id,
                       contact.owner_user_id AS contact_owner_user_id,
                       contact.pool_type AS contact_pool_type
                FROM contactout_enrichment_jobs job
                JOIN contactout_accounts account ON account.id = job.account_id
                JOIN contacts contact ON contact.id = job.contact_id
                WHERE job.id = %s AND job.status = 'running'
                  AND job.lease_token = %s AND job.quota_reserved = FALSE
                FOR UPDATE OF job, account, contact
                """,
                (job_id, lease_token),
            ).fetchone()
            if not job:
                return "stale_lease"
            if job["lease_expires_at"] <= datetime.now(UTC):
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'blocked', error_code = 'lease_expired_unknown_charge',
                        lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job_id, lease_token),
                )
                return "lease_expired_unknown_charge"
            if job["account_status"] != "active" or job["assigned_user_id"] != job["owner_user_id"]:
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'blocked', error_code = 'account_assignment_changed',
                        lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job_id, lease_token),
                )
                return "account_assignment_changed"
            if job["contact_pool_type"] != "private" or job["contact_owner_user_id"] != job["owner_user_id"]:
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'blocked', error_code = 'ownership_changed',
                        lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job_id, lease_token),
                )
                return "ownership_changed"
            if job["cooldown_until"] and job["cooldown_until"] > datetime.now(UTC):
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'retry_wait', error_code = 'account_cooldown',
                        next_attempt_at = %s, lease_token = NULL,
                        lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job["cooldown_until"], job_id, lease_token),
                )
                return "account_cooldown"
            account_scope = f"account:{job['account_id']}"
            scopes = [("global", int(global_limit)), (account_scope, int(job["daily_limit"]))]
            if any(limit <= 0 for _, limit in scopes):
                return "daily_quota_exhausted"
            for scope, _ in scopes:
                conn.execute(
                    """
                    INSERT INTO provider_account_daily_usage(provider, scope_key, usage_date)
                    VALUES ('contactout', %s, timezone('Asia/Shanghai', NOW())::date)
                    ON CONFLICT DO NOTHING
                    """,
                    (scope,),
                )
            rows = {
                row["scope_key"]: row
                for row in conn.execute(
                    """
                    SELECT * FROM provider_account_daily_usage
                    WHERE provider = 'contactout'
                      AND usage_date = timezone('Asia/Shanghai', NOW())::date
                      AND scope_key IN (%s, %s)
                    ORDER BY scope_key
                    FOR UPDATE
                    """,
                    ("global", account_scope),
                ).fetchall()
            }
            units = int(job["quota_units"])
            allowed = all(int(rows[scope]["reserved_units"]) + int(rows[scope]["used_units"]) + units <= limit for scope, limit in scopes)
            if not allowed:
                conn.execute(
                    """
                    UPDATE provider_account_daily_usage
                    SET denied_count = denied_count + 1, updated_at = NOW()
                    WHERE provider = 'contactout'
                      AND usage_date = timezone('Asia/Shanghai', NOW())::date
                      AND scope_key IN (%s, %s)
                    """,
                    ("global", account_scope),
                )
                return "daily_quota_exhausted"
            conn.execute(
                """
                UPDATE provider_account_daily_usage
                SET reserved_units = reserved_units + %s, updated_at = NOW()
                WHERE provider = 'contactout'
                  AND usage_date = timezone('Asia/Shanghai', NOW())::date
                  AND scope_key IN (%s, %s)
                """,
                (units, "global", account_scope),
            )
            conn.execute(
                """
                UPDATE contactout_enrichment_jobs
                SET attempts = attempts + 1,
                    quota_reserved = TRUE,
                    quota_usage_date = timezone('Asia/Shanghai', NOW())::date,
                    updated_at = NOW()
                WHERE id = %s AND status = 'running' AND lease_token = %s
                  AND quota_reserved = FALSE
                """,
                (job_id, lease_token),
            )
            return "reserved"

    def complete_contactout_job(self, job_id: int, lease_token: str, *, status: str, result: dict[str, Any]) -> bool:
        with self.db.connect() as conn:
            job = conn.execute(
                """
                SELECT * FROM contactout_enrichment_jobs
                WHERE id = %s AND status = 'running' AND lease_token = %s
                  AND lease_expires_at > NOW()
                FOR UPDATE
                """,
                (job_id, lease_token),
            ).fetchone()
            if not job:
                return False
            account = conn.execute(
                """
                SELECT id FROM contactout_accounts
                WHERE id = %s AND status = 'active' AND assigned_user_id = %s
                FOR UPDATE
                """,
                (job["account_id"], job["owner_user_id"]),
            ).fetchone()
            if not account:
                self._settle_contactout_quota(conn, job, consumed=True)
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'blocked', error_code = 'account_reassigned', completed_at = NOW(),
                        lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job_id, lease_token),
                )
                return False
            contact = conn.execute(
                """
                SELECT * FROM contacts
                WHERE id = %s AND pool_type = 'private' AND owner_user_id = %s
                FOR UPDATE
                """,
                (job["contact_id"], job["owner_user_id"]),
            ).fetchone()
            if not contact:
                self._settle_contactout_quota(conn, job, consumed=True)
                conn.execute(
                    """
                    UPDATE contactout_enrichment_jobs
                    SET status = 'blocked', error_code = 'ownership_changed', completed_at = NOW(),
                        lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job_id, lease_token),
                )
                return False
            email_candidates = _merge_contact_candidates(contact.get("email_candidates") or [], result.get("email_candidates") or [], "email")
            phone_candidates = _merge_contact_candidates(contact.get("phone_candidates") or [], result.get("phone_candidates") or [], "phone")
            selected = None if result.get("review_required") else next(
                (
                    item for item in result.get("email_candidates") or []
                    if item.get("source") == "contactout"
                    and item.get("status") == "valid"
                    and item.get("category") == "personal_work"
                    and int(item.get("confidence") or 0) >= 80
                ),
                None,
            )
            conn.execute(
                """
                UPDATE contacts
                SET email_candidates = %s::jsonb,
                    phone_candidates = %s::jsonb,
                    email = COALESCE(%s, email),
                    email_status = CASE WHEN %s::text IS NOT NULL THEN 'valid' ELSE email_status END,
                    email_source = CASE WHEN %s::text IS NOT NULL THEN 'contactout' ELSE email_source END,
                    email_confidence = COALESCE(%s, email_confidence),
                    status = CASE WHEN %s::text IS NOT NULL THEN 'enriched' ELSE status END,
                    enriched_at = CASE WHEN %s::text IS NOT NULL THEN NOW() ELSE enriched_at END
                WHERE id = %s AND owner_user_id = %s AND pool_type = 'private'
                """,
                (
                    json.dumps(email_candidates), json.dumps(phone_candidates),
                    selected.get("email") if selected else None,
                    selected.get("email") if selected else None,
                    selected.get("email") if selected else None,
                    selected.get("confidence") if selected else None,
                    selected.get("email") if selected else None,
                    selected.get("email") if selected else None,
                    job["contact_id"], job["owner_user_id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO contactout_enrichment_results(
                    job_id, contact_id, match_status, match_confidence, review_required,
                    profile_url, email_candidates, phone_candidates
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (job_id) DO NOTHING
                """,
                (
                    job_id, job["contact_id"], result.get("match_status") or status,
                    int(result.get("match_confidence") or 0), bool(result.get("review_required")),
                    result.get("profile_url"), json.dumps(result.get("email_candidates") or []),
                    json.dumps(result.get("phone_candidates") or []),
                ),
            )
            self._settle_contactout_quota(conn, job, consumed=True)
            conn.execute(
                """
                UPDATE contactout_enrichment_jobs
                SET status = %s, completed_at = NOW(), updated_at = NOW(),
                    lease_token = NULL, lease_expires_at = NULL, error_code = NULL
                WHERE id = %s AND status = 'running' AND lease_token = %s
                """,
                (status, job_id, lease_token),
            )
            conn.execute("UPDATE contactout_accounts SET last_used_at = NOW(), updated_at = NOW() WHERE id = %s", (job["account_id"],))
            return True

    def retry_contactout_job(self, job_id: int, lease_token: str, error_code: str, *, retry_after_seconds: int) -> None:
        with self.db.connect() as conn:
            job = conn.execute(
                """
                SELECT * FROM contactout_enrichment_jobs
                WHERE id = %s AND status = 'running' AND lease_token = %s
                  AND lease_expires_at > NOW()
                FOR UPDATE
                """,
                (job_id, lease_token),
            ).fetchone()
            if not job:
                return
            self._settle_contactout_quota(conn, job, consumed=False)
            conn.execute(
                """
                UPDATE contactout_enrichment_jobs
                SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'retry_wait' END,
                    next_attempt_at = NOW() + (%s * INTERVAL '1 second'), error_code = %s,
                    lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'running' AND lease_token = %s
                """,
                (max(60, int(retry_after_seconds)), error_code, job_id, lease_token),
            )
            if error_code == "rate_limited":
                conn.execute(
                    "UPDATE contactout_accounts SET cooldown_until = NOW() + (%s * INTERVAL '1 second'), updated_at = NOW() WHERE id = %s",
                    (max(60, int(retry_after_seconds)), job["account_id"]),
                )

    def block_contactout_job(self, job_id: int, lease_token: str, error_code: str) -> None:
        account_to_fence = None
        with self.db.connect() as conn:
            job = conn.execute(
                """
                SELECT * FROM contactout_enrichment_jobs
                WHERE id = %s AND status = 'running' AND lease_token = %s
                  AND lease_expires_at > NOW()
                FOR UPDATE
                """,
                (job_id, lease_token),
            ).fetchone()
            if not job:
                return
            self._settle_contactout_quota(conn, job, consumed=False)
            conn.execute(
                """
                UPDATE contactout_enrichment_jobs
                SET status = 'blocked', error_code = %s, lease_token = NULL,
                    lease_expires_at = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'running' AND lease_token = %s
                """,
                (error_code, job_id, lease_token),
            )
            if error_code in {"challenge_required", "reauth_required"}:
                conn.execute(
                    "UPDATE contactout_accounts SET status = %s, updated_at = NOW() WHERE id = %s",
                    (error_code, job["account_id"]),
                )
                account_to_fence = job["account_id"]
        if account_to_fence is not None:
            self._fence_contactout_account_jobs(account_to_fence)

    def fail_contactout_job(self, job_id: int, lease_token: str, error_code: str, *, consumed: bool) -> None:
        with self.db.connect() as conn:
            job = conn.execute(
                """
                SELECT * FROM contactout_enrichment_jobs
                WHERE id = %s AND status = 'running' AND lease_token = %s
                  AND lease_expires_at > NOW()
                FOR UPDATE
                """,
                (job_id, lease_token),
            ).fetchone()
            if not job:
                return
            self._settle_contactout_quota(conn, job, consumed=consumed)
            conn.execute(
                """
                UPDATE contactout_enrichment_jobs
                SET status = 'failed', error_code = %s, completed_at = NOW(),
                    lease_token = NULL, lease_expires_at = NULL, updated_at = NOW()
                WHERE id = %s AND status = 'running' AND lease_token = %s
                """,
                (error_code, job_id, lease_token),
            )

    @staticmethod
    def _settle_contactout_quota(conn: Any, job: dict[str, Any], *, consumed: bool) -> None:
        if not job.get("quota_reserved") or not job.get("quota_usage_date"):
            return
        account_scope = f"account:{job['account_id']}"
        units = int(job.get("quota_units") or 1)
        conn.execute(
            """
            UPDATE provider_account_daily_usage
            SET reserved_units = GREATEST(0, reserved_units - %s),
                used_units = used_units + %s,
                updated_at = NOW()
            WHERE provider = 'contactout' AND usage_date = %s
              AND scope_key IN (%s, %s)
            """,
            (units, units if consumed else 0, job["quota_usage_date"], "global", account_scope),
        )
        conn.execute(
            """
            UPDATE contactout_enrichment_jobs
            SET quota_reserved = FALSE, updated_at = NOW()
            WHERE id = %s
            """,
            (job["id"],),
        )

    def list_for_social_enrichment(self, limit: int, *, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        owner_filter, owner_params = self._owner_filter("contacts", user, prefix="AND")
        with self.db.connect() as conn:
            return conn.execute(
                f"""
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
                  {owner_filter}
                ORDER BY social_enriched_at NULLS FIRST, created_at DESC
                LIMIT %s
                """,
                tuple(owner_params + [limit]),
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
            sabcd = conn.execute(
                f"""
                SELECT sabcd_stage, COUNT(*) AS count
                FROM contacts c
                {owner_where}
                GROUP BY sabcd_stage
                """,
                tuple(owner_params),
            ).fetchall()
            action_rows = conn.execute(
                f"""
                SELECT id, first_name, last_name, company_name, lifecycle_stage, sabcd_stage, disposition,
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
            "sabcd": {row["sabcd_stage"]: int(row["count"]) for row in sabcd},
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
        sabcd_stage: str | None = None,
    ) -> None:
        sabcd_stage = stage_from_payload(sabcd_stage)
        private_days = _private_pool_days(self.db.config.raw)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET last_stage_changed_at = CASE
                        WHEN COALESCE(
                            %s,
                            CASE
                                WHEN COALESCE(%s, lifecycle_stage) IN ('signed', 'maintenance') OR COALESCE(%s, disposition) = 'won' THEN 'S'
                                WHEN COALESCE(%s, lifecycle_stage) IN ('business_plan', 'trial_order', 'agency_agreement', 'store_creation', 'store_visit', 'hq_visit') THEN CASE WHEN sabcd_stage = 'S' THEN 'S' ELSE 'A' END
                                WHEN COALESCE(%s, lifecycle_stage) IN ('conversation', 'meeting') THEN CASE WHEN sabcd_stage IN ('D', 'C') THEN 'B' ELSE sabcd_stage END
                                WHEN COALESCE(%s, lifecycle_stage) = 'replied' THEN CASE WHEN sabcd_stage = 'D' THEN 'C' ELSE sabcd_stage END
                                ELSE sabcd_stage
                            END
                        ) IS DISTINCT FROM sabcd_stage THEN NOW()
                        ELSE last_stage_changed_at
                    END,
                    pool_expires_at = CASE
                        WHEN pool_type = 'private'
                         AND COALESCE(
                            %s,
                            CASE
                                WHEN COALESCE(%s, lifecycle_stage) IN ('signed', 'maintenance') OR COALESCE(%s, disposition) = 'won' THEN 'S'
                                WHEN COALESCE(%s, lifecycle_stage) IN ('business_plan', 'trial_order', 'agency_agreement', 'store_creation', 'store_visit', 'hq_visit') THEN CASE WHEN sabcd_stage = 'S' THEN 'S' ELSE 'A' END
                                WHEN COALESCE(%s, lifecycle_stage) IN ('conversation', 'meeting') THEN CASE WHEN sabcd_stage IN ('D', 'C') THEN 'B' ELSE sabcd_stage END
                                WHEN COALESCE(%s, lifecycle_stage) = 'replied' THEN CASE WHEN sabcd_stage = 'D' THEN 'C' ELSE sabcd_stage END
                                ELSE sabcd_stage
                            END
                         ) IS DISTINCT FROM sabcd_stage THEN NOW() + (%s::text || ' days')::interval
                        ELSE pool_expires_at
                    END,
                    lifecycle_stage = COALESCE(%s, lifecycle_stage),
                    disposition = COALESCE(%s, disposition),
                    next_action_at = COALESCE(%s::timestamptz, next_action_at),
                    notes = COALESCE(%s, notes),
                    lost_reason = COALESCE(%s, lost_reason),
                    owner = COALESCE(%s, owner),
                    sabcd_stage = COALESCE(
                        %s,
                        CASE
                            WHEN COALESCE(%s, lifecycle_stage) IN ('signed', 'maintenance') OR COALESCE(%s, disposition) = 'won' THEN 'S'
                            WHEN COALESCE(%s, lifecycle_stage) IN ('business_plan', 'trial_order', 'agency_agreement', 'store_creation', 'store_visit', 'hq_visit') THEN CASE WHEN sabcd_stage = 'S' THEN 'S' ELSE 'A' END
                            WHEN COALESCE(%s, lifecycle_stage) IN ('conversation', 'meeting') THEN CASE WHEN sabcd_stage IN ('D', 'C') THEN 'B' ELSE sabcd_stage END
                            WHEN COALESCE(%s, lifecycle_stage) = 'replied' THEN CASE WHEN sabcd_stage = 'D' THEN 'C' ELSE sabcd_stage END
                            ELSE sabcd_stage
                        END
                    )
                WHERE id = %s
                """,
                (
                    sabcd_stage,
                    lifecycle_stage,
                    disposition,
                    lifecycle_stage,
                    lifecycle_stage,
                    lifecycle_stage,
                    sabcd_stage,
                    lifecycle_stage,
                    disposition,
                    lifecycle_stage,
                    lifecycle_stage,
                    lifecycle_stage,
                    private_days,
                    lifecycle_stage,
                    disposition,
                    next_action_at,
                    notes,
                    lost_reason,
                    owner,
                    sabcd_stage,
                    lifecycle_stage,
                    disposition,
                    lifecycle_stage,
                    lifecycle_stage,
                    lifecycle_stage,
                    contact_id,
                ),
            )
        if sabcd_stage == "S" or lifecycle_stage in {"signed", "maintenance"} or disposition == "won":
            self.refresh_customer_profile_snapshot(contact_id)

    def claim_public_contact(self, contact_id: int, user: dict[str, Any]) -> dict[str, Any]:
        private_days = _private_pool_days(self.db.config.raw)
        with self.db.connect() as conn:
            row = conn.execute(
                """
                UPDATE contacts
                SET pool_type = 'private',
                    owner_user_id = %s,
                    owner = %s,
                    assignment_source = 'manual_claim',
                    assigned_at = NOW(),
                    pool_expires_at = NOW() + (%s::text || ' days')::interval,
                    returned_to_public_at = NULL,
                    claim_count = claim_count + 1
                WHERE id = %s
                  AND pool_type = 'public'
                  AND sabcd_stage <> 'S'
                RETURNING *
                """,
                (user["id"], user.get("display_name") or user.get("username"), private_days, contact_id),
            ).fetchone()
            if not row:
                raise RuntimeError("Contact is not available in public pool")
            return _with_customer_intelligence(row)

    def return_contact_to_public(self, contact_id: int, user: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
        params: list[Any] = [reason, contact_id]
        owner_clause = ""
        if user.get("role") != "admin":
            owner_clause = "AND owner_user_id = %s"
            params.append(user["id"])
        with self.db.connect() as conn:
            row = conn.execute(
                f"""
                UPDATE contacts
                SET pool_type = 'public',
                    owner_user_id = NULL,
                    owner = NULL,
                    assignment_source = COALESCE(%s, 'manual_return'),
                    assigned_at = NULL,
                    pool_expires_at = NULL,
                    returned_to_public_at = NOW(),
                    disposition = CASE WHEN disposition = 'won' THEN disposition ELSE 'active' END
                WHERE id = %s
                  AND sabcd_stage <> 'S'
                  {owner_clause}
                RETURNING *
                """,
                tuple(params),
            ).fetchone()
            if not row:
                raise RuntimeError("Contact cannot be returned to public pool")
            return _with_customer_intelligence(row)

    def recycle_stale_private_pool(self, *, limit: int = 100) -> int:
        private_days = _private_pool_days(self.db.config.raw)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                UPDATE contacts
                SET pool_type = 'public',
                    owner_user_id = NULL,
                    owner = NULL,
                    assignment_source = 'stale_recycle',
                    assigned_at = NULL,
                    pool_expires_at = NULL,
                    returned_to_public_at = NOW()
                WHERE id IN (
                    SELECT id
                    FROM contacts
                    WHERE pool_type = 'private'
                      AND sabcd_stage <> 'S'
                      AND COALESCE(last_stage_changed_at, assigned_at, created_at) <= NOW() - (%s::text || ' days')::interval
                      AND COALESCE(assigned_at, created_at) <= NOW() - (%s::text || ' days')::interval
                    ORDER BY pool_expires_at NULLS FIRST, created_at
                    LIMIT %s
                )
                RETURNING id
                """,
                (private_days, private_days, limit),
            ).fetchall()
            return len(rows)

    def close_expired_outreach_sequences(self, *, wait_days: int = 14, limit: int = 100) -> dict[str, int]:
        """Close three-touch sequences after a cooling period based on observed engagement."""
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                WITH candidates AS (
                    SELECT c.id,
                           EXISTS (
                               SELECT 1 FROM email_events e
                               WHERE e.contact_id = c.id AND e.event_type IN ('opened', 'clicked')
                           ) AS engaged
                    FROM contacts c
                    WHERE c.status = 'sent_3'
                      AND c.sequence_step >= 3
                      AND c.last_contacted_at <= NOW() - (%s::text || ' days')::interval
                      AND c.replied_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM email_events e
                          WHERE e.contact_id = c.id AND e.event_type = 'replied'
                      )
                    ORDER BY c.last_contacted_at
                    LIMIT %s
                )
                UPDATE contacts c
                SET lifecycle_stage = CASE WHEN candidates.engaged THEN 'waiting_pool' ELSE 'abandoned' END,
                    disposition = CASE WHEN candidates.engaged THEN 'waiting' ELSE 'abandoned' END,
                    next_action_at = CASE WHEN candidates.engaged THEN NOW() + INTERVAL '30 days' ELSE NULL END,
                    last_stage_changed_at = NOW(),
                    notes = COALESCE(c.notes, CASE WHEN candidates.engaged
                        THEN 'Three-touch sequence completed; engaged without reply.'
                        ELSE 'Three-touch sequence completed without engagement.' END)
                FROM candidates
                WHERE c.id = candidates.id
                RETURNING candidates.engaged
                """,
                (max(1, wait_days), limit),
            ).fetchall()
        waiting = sum(1 for row in rows if row.get("engaged"))
        return {"waiting": waiting, "abandoned": len(rows) - waiting}

    def get_app_setting(self, key: str, default: Any = None) -> Any:
        with self.db.connect() as conn:
            row = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key = %s", (key,)).fetchone()
        return row["setting_value"] if row else default

    def set_app_setting(self, key: str, value: Any, *, user_id: int | None = None) -> Any:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_by_user_id, updated_at)
                VALUES (%s, %s::jsonb, %s, NOW())
                ON CONFLICT(setting_key) DO UPDATE
                SET setting_value = EXCLUDED.setting_value,
                    updated_by_user_id = EXCLUDED.updated_by_user_id,
                    updated_at = NOW()
                RETURNING setting_value
                """,
                (key, json.dumps(value, ensure_ascii=False), user_id),
            ).fetchone()
        return row["setting_value"]

    def region_assignment_rules(self) -> list[dict[str, Any]]:
        stored = self.get_app_setting("customer_pool.region_assignments", [])
        if isinstance(stored, list) and stored:
            return stored
        return _region_assignment_rules(self.db.config.raw)

    def auto_assign_public_pool(self, *, limit: int = 100, contact_ids: list[int] | None = None) -> dict[str, Any]:
        rules = self.region_assignment_rules()
        if not rules:
            return {"assigned": 0, "skipped": 0, "missing_rules": True}
        users = {str(row["username"]).lower(): row for row in self.list_users() if row.get("active")}
        users.update({str(row["display_name"]).lower(): row for row in self.list_users() if row.get("active")})
        private_days = _private_pool_days(self.db.config.raw)
        assigned = skipped = 0
        with self.db.connect() as conn:
            id_clause = ""
            params: list[Any] = []
            if contact_ids:
                id_clause = "AND id = ANY(%s)"
                params.append(contact_ids)
            params.append(limit)
            contacts = conn.execute(
                f"""
                SELECT id, location, company_name, source_context
                FROM contacts
                WHERE pool_type = 'public'
                  AND sabcd_stage <> 'S'
                  {id_clause}
                ORDER BY created_at
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
            for contact in contacts:
                owner = _match_region_owner(contact, rules, users)
                if not owner:
                    skipped += 1
                    continue
                row = conn.execute(
                    """
                    UPDATE contacts
                    SET pool_type = 'private',
                        owner_user_id = %s,
                        owner = %s,
                        assignment_source = 'auto_region',
                        assigned_at = NOW(),
                        pool_expires_at = NOW() + (%s::text || ' days')::interval,
                        returned_to_public_at = NULL
                    WHERE id = %s
                      AND pool_type = 'public'
                    RETURNING id
                    """,
                    (owner["id"], owner.get("display_name") or owner.get("username"), private_days, contact["id"]),
                ).fetchone()
                assigned += 1 if row else 0
                skipped += 0 if row else 1
        return {"assigned": assigned, "skipped": skipped, "missing_rules": False}

    def assign_public_contacts_to_owner(
        self,
        contact_ids: list[int],
        *,
        owner_user_id: int,
        owner_name: str,
        assignment_source: str = "direct_import",
    ) -> dict[str, Any]:
        """Assign newly sourced public contacts to the salesperson who uploaded them."""
        contact_ids = [int(contact_id) for contact_id in contact_ids if contact_id]
        if not contact_ids:
            return {
                "assigned": 0,
                "skipped": 0,
                "missing_rules": False,
                "mode": assignment_source,
                "owner_user_id": owner_user_id,
                "owner": owner_name,
            }
        private_days = _private_pool_days(self.db.config.raw)
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                UPDATE contacts
                SET pool_type = 'private',
                    owner_user_id = %s,
                    owner = %s,
                    assignment_source = %s,
                    assigned_at = NOW(),
                    pool_expires_at = NOW() + (%s::text || ' days')::interval,
                    returned_to_public_at = NULL
                WHERE id = ANY(%s)
                  AND pool_type = 'public'
                  AND sabcd_stage <> 'S'
                RETURNING id
                """,
                (owner_user_id, owner_name, assignment_source, private_days, contact_ids),
            ).fetchall()
        assigned = len(rows)
        return {
            "assigned": assigned,
            "skipped": len(contact_ids) - assigned,
            "missing_rules": False,
            "mode": assignment_source,
            "owner_user_id": owner_user_id,
            "owner": owner_name,
        }

    def list_contacts_for_search_tasks(self, task_ids: list[int]) -> list[dict[str, Any]]:
        if not task_ids:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*
                FROM contacts c
                WHERE c.search_task_id = ANY(%s)
                ORDER BY c.id
                """,
                (task_ids,),
            ).fetchall()
        return [_with_customer_intelligence(row) for row in rows]

    def refresh_customer_profile_snapshot(self, contact_id: int) -> None:
        contact = self.get_contact(contact_id)
        if not contact:
            return
        snapshot = build_customer_profile(contact)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET customer_profile_snapshot = %s::jsonb
                WHERE id = %s
                """,
                (json.dumps(snapshot, ensure_ascii=False), contact_id),
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

    def get_active_icp_profile(self, *, owner_user_id: int | None = None) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM icp_profiles
                WHERE status = 'active' AND (owner_user_id = %s OR owner_user_id IS NULL)
                ORDER BY (owner_user_id IS NOT NULL) DESC, version DESC, id DESC
                LIMIT 1
                """,
                (owner_user_id,),
            ).fetchone()
        if not row:
            return default_icp_profile()
        criteria = row.get("criteria") if isinstance(row.get("criteria"), dict) else {}
        return {
            **default_icp_profile(),
            **criteria,
            "id": int(row["id"]),
            "name": row["name"],
            "version": int(row.get("version") or 1),
            "qualified_threshold": int(row.get("qualified_threshold") or 70),
            "review_threshold": int(row.get("review_threshold") or 50),
            "disqualifiers": row.get("disqualifiers") or [],
        }

    def update_contact_icp_assessment(
        self,
        contact_id: int,
        assessment: dict[str, Any],
        *,
        profile_id: int | None = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET icp_profile_id = COALESCE(%s, icp_profile_id),
                    icp_assessment = %s::jsonb,
                    lead_score = COALESCE(%s, lead_score),
                    customer_profile_snapshot = customer_profile_snapshot || %s::jsonb
                WHERE id = %s
                """,
                (
                    profile_id,
                    json.dumps(assessment, ensure_ascii=False),
                    assessment.get("score"),
                    json.dumps({"icp_assessment": assessment}, ensure_ascii=False),
                    contact_id,
                ),
            )

    def record_icp_feedback(
        self,
        contact_id: int,
        *,
        reviewer_user_id: int,
        expected_qualified: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        contact = self.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        profile = self.get_active_icp_profile(owner_user_id=contact.get("owner_user_id"))
        profile_id = profile.get("id")
        if not profile_id:
            raise RuntimeError("Active ICP profile is not persisted")
        assessment = contact.get("icp_assessment") if isinstance(contact.get("icp_assessment"), dict) else {}
        predicted = bool(assessment.get("qualified"))
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO icp_feedback(
                    profile_id, contact_id, reviewer_user_id,
                    predicted_qualified, expected_qualified, reason
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (profile_id, contact_id, reviewer_user_id)
                DO UPDATE SET
                    predicted_qualified = EXCLUDED.predicted_qualified,
                    expected_qualified = EXCLUDED.expected_qualified,
                    reason = EXCLUDED.reason,
                    created_at = NOW()
                RETURNING *
                """,
                (profile_id, contact_id, reviewer_user_id, predicted, expected_qualified, reason),
            ).fetchone()

    def update_icp_profile_threshold(
        self,
        profile_id: int,
        *,
        qualified_threshold: int,
    ) -> dict[str, Any] | None:
        threshold = max(40, min(90, int(qualified_threshold)))
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE icp_profiles
                SET qualified_threshold = %s,
                    review_threshold = LEAST(review_threshold, %s - 5),
                    version = version + 1,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (threshold, threshold, profile_id),
            ).fetchone()

    def create_outbound_experiment(
        self,
        *,
        name: str,
        hypothesis: str,
        variable_name: str,
        variants: list[dict[str, Any]],
        owner_user_id: int | None,
        campaign_id: int | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        if len(variants) < 2:
            raise ValueError("An experiment requires at least two variants")
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO outbound_experiments(
                    name, hypothesis, variable_name, variants, owner_user_id,
                    campaign_id, status, started_at
                )
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s,
                        CASE WHEN %s = 'active' THEN NOW() ELSE NULL END)
                RETURNING *
                """,
                (
                    name.strip(),
                    hypothesis.strip(),
                    variable_name.strip(),
                    json.dumps(variants, ensure_ascii=False),
                    owner_user_id,
                    campaign_id,
                    status,
                    status,
                ),
            ).fetchone()

    def get_active_outbound_experiment(self, *, owner_user_id: int | None = None) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM outbound_experiments
                WHERE status = 'active' AND (owner_user_id = %s OR owner_user_id IS NULL)
                ORDER BY (owner_user_id IS NOT NULL) DESC, started_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                (owner_user_id,),
            ).fetchone()

    def update_outbound_experiment(self, experiment_id: int, *, status: str) -> dict[str, Any] | None:
        if status not in {"draft", "active", "completed", "cancelled"}:
            raise ValueError("Unsupported experiment status")
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE outbound_experiments
                SET status = %s,
                    started_at = CASE WHEN %s = 'active' THEN COALESCE(started_at, NOW()) ELSE started_at END,
                    ended_at = CASE WHEN %s IN ('completed', 'cancelled') THEN NOW() ELSE NULL END,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (status, status, status, experiment_id),
            ).fetchone()

    def update_icp_from_reply(
        self,
        contact_id: int,
        reply_classification: dict[str, Any],
    ) -> None:
        """Feed reply classification signals back into ICP assessment to close the learning loop."""
        contact = self.get_contact(contact_id)
        if not contact:
            return
        current = contact.get("icp_assessment") or {}
        if not isinstance(current, dict):
            current = {}
        if "assessment_before_outcome" not in current:
            current["assessment_before_outcome"] = {
                key: current[key]
                for key in ("qualified", "score", "tier", "profile_name", "profile_version")
                if key in current
            }
        label = reply_classification.get("label", "")
        positive = reply_classification.get("positive", False)
        score_shift = 0
        tags = list(current.get("reply_signals") or [])
        new_signals: dict[str, Any] = {}
        if label in ("positive_interested", "positive_soft", "positive_referral"):
            score_shift = 10
            tags.append("reply_validated")
            new_signals = {"last_reply_signal": "positive", "validated_at": datetime.now(UTC).isoformat()}
            if not current.get("qualified"):
                current["tier"] = "review"
                current["qualified"] = True
                current["score"] = max(60, (current.get("score") or 0) + score_shift)
        elif label in ("negative_notfit", "negative_hostile"):
            score_shift = -15
            tags.append("reply_not_fit")
            new_signals = {"last_reply_signal": "negative", "notfit_at": datetime.now(UTC).isoformat()}
            current["tier"] = "disqualified"
            current["qualified"] = False
            current["score"] = min(current.get("score") or 40, 35)
        elif label == "negative_notnow":
            score_shift = -5
            tags.append("reply_not_now")
            new_signals = {"last_reply_signal": "notnow", "notnow_at": datetime.now(UTC).isoformat()}
        else:
            return
        current["score"] = max(0, min(100, (current.get("score") or 0) + score_shift))
        current["reply_signals"] = tags
        current["reply_signal_detail"] = new_signals
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET icp_assessment = %s::jsonb
                WHERE id = %s
                """,
                (json.dumps(current, ensure_ascii=False), contact_id),
            )

    def list_flywheel_contact_rows(self, *, window_days: int = 30) -> list[dict[str, Any]]:
        """Return one outcome row per contact for explainable strategy aggregation."""
        days = max(1, min(int(window_days), 365))
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT
                  c.id, c.job_title, c.industry, c.location, c.icp_assessment,
                  lead_meta.country, lead_meta.region,
                  c.lifecycle_stage, c.disposition, c.source_context,
                  COALESCE(event_flags.sent, 0)::integer AS sent,
                  CASE WHEN COALESCE(event_flags.sent, 0) > 0 THEN COALESCE(event_flags.delivered, 0) ELSE 0 END::integer AS delivered,
                  CASE WHEN COALESCE(event_flags.sent, 0) > 0 THEN COALESCE(event_flags.opened, 0) ELSE 0 END::integer AS opened,
                  CASE WHEN COALESCE(event_flags.sent, 0) > 0 THEN COALESCE(event_flags.replied, 0) ELSE 0 END::integer AS replied,
                  CASE WHEN COALESCE(event_flags.sent, 0) > 0 AND COALESCE(event_flags.replied, 0) > 0
                       THEN GREATEST(COALESCE(event_flags.positive_replies, 0), COALESCE(reply_flags.positive, 0)) ELSE 0 END::integer AS positive_replies,
                  CASE WHEN COALESCE(event_flags.sent, 0) > 0 AND COALESCE(event_flags.replied, 0) > 0
                       THEN GREATEST(COALESCE(event_flags.negative_replies, 0), COALESCE(reply_flags.negative, 0)) ELSE 0 END::integer AS negative_replies,
                  COALESCE(reply_flags.positive, 0)::integer AS positive_outcomes,
                  COALESCE(reply_flags.negative, 0)::integer AS negative_outcomes,
                  CASE WHEN COALESCE(event_flags.sent, 0) > 0 THEN COALESCE(event_flags.bounced, 0) ELSE 0 END::integer AS bounced,
                  CASE WHEN COALESCE(event_flags.sent, 0) > 0 THEN COALESCE(event_flags.unsubscribed, 0) ELSE 0 END::integer AS unsubscribed,
                  CASE WHEN c.lifecycle_stage IN ('meeting', 'business_plan', 'store_visit', 'trial_order', 'agency_agreement', 'hq_visit', 'signed', 'maintenance') THEN 1 ELSE 0 END AS meetings,
                  CASE WHEN c.lifecycle_stage IN ('signed', 'maintenance') OR c.disposition = 'won' THEN 1 ELSE 0 END AS won,
                  CASE WHEN c.lifecycle_stage = 'abandoned' OR c.disposition IN ('abandoned', 'lost') THEN 1 ELSE 0 END AS lost
                FROM contacts c
                LEFT JOIN LATERAL (
                  SELECT
                    COUNT(*)::integer AS event_count,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'sent')::integer AS sent,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'delivered')::integer AS delivered,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'opened')::integer AS opened,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'replied')::integer AS replied,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'replied' AND metadata->'reply_classification'->>'positive' = 'true')::integer AS positive_replies,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'replied' AND metadata->'reply_classification'->>'label' = 'negative_notfit')::integer AS negative_replies,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'bounced')::integer AS bounced,
                    COUNT(DISTINCT event_type) FILTER (WHERE event_type = 'unsubscribed')::integer AS unsubscribed
                  FROM email_events
                  WHERE email_events.contact_id = c.id
                    AND email_events.occurred_at >= NOW() - (%s::text || ' days')::interval
                ) event_flags ON TRUE
                LEFT JOIN LATERAL (
                  SELECT
                    COUNT(*)::integer AS event_count,
                    MAX(CASE WHEN metadata->'reply_classification'->>'positive' = 'true'
                               OR outcome IN ('positive_interested', 'positive_soft', 'positive_referral') THEN 1 ELSE 0 END)::integer AS positive,
                    MAX(CASE WHEN metadata->'reply_classification'->>'label' = 'negative_notfit'
                               OR outcome = 'negative_notfit' THEN 1 ELSE 0 END)::integer AS negative
                  FROM interactions
                  WHERE interactions.contact_id = c.id
                    AND interactions.interaction_type IN ('email_reply', 'phone_call')
                    AND interactions.occurred_at >= NOW() - (%s::text || ' days')::interval
                ) reply_flags ON TRUE
                LEFT JOIN LATERAL (
                  SELECT MAX(country) AS country, MAX(region) AS region
                  FROM leads
                  WHERE leads.contact_id = c.id
                ) lead_meta ON TRUE
                WHERE c.created_at >= NOW() - (%s::text || ' days')::interval
                   OR COALESCE(event_flags.event_count, 0) > 0
                   OR COALESCE(reply_flags.event_count, 0) > 0
                """,
                (days, days, days),
            ).fetchall()

    def save_flywheel_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE flywheel_strategy_snapshots
                SET status = 'superseded', updated_at = NOW()
                WHERE scope_type = %s AND scope_key = %s AND status = 'active'
                """,
                (snapshot["scope_type"], snapshot["scope_key"]),
            )
            return conn.execute(
                """
                INSERT INTO flywheel_strategy_snapshots(
                  scope_type, scope_key, version, status, window_start, window_end,
                  sample_size, metrics, rules, guidance, evidence
                )
                VALUES (%s, %s,
                  COALESCE((
                    SELECT MAX(version) + 1 FROM flywheel_strategy_snapshots
                    WHERE scope_type = %s AND scope_key = %s
                  ), 1),
                  %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
                )
                RETURNING *
                """,
                (
                    snapshot["scope_type"], snapshot["scope_key"],
                    snapshot["scope_type"], snapshot["scope_key"],
                    snapshot["status"], snapshot["window_start"], snapshot["window_end"],
                    int(snapshot.get("sample_size") or 0),
                    json.dumps(snapshot.get("metrics") or {}, ensure_ascii=False),
                    json.dumps(snapshot.get("rules") or {}, ensure_ascii=False),
                    json.dumps(snapshot.get("guidance") or {}, ensure_ascii=False),
                    json.dumps(snapshot.get("evidence") or [], ensure_ascii=False),
                ),
            ).fetchone()

    def get_active_flywheel_snapshot(self, *, scope_type: str, scope_key: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM flywheel_strategy_snapshots
                WHERE scope_type = %s AND scope_key = %s AND status = 'active'
                ORDER BY version DESC, updated_at DESC
                LIMIT 1
                """,
                (scope_type, scope_key),
            ).fetchone()

    def flywheel_summary(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT scope_type, scope_key, version, status, window_start, window_end,
                       sample_size, metrics, rules, guidance, updated_at
                FROM flywheel_strategy_snapshots
                WHERE status = 'active'
                ORDER BY scope_type, scope_key
                """
            ).fetchall()
            learning = conn.execute(
                """
                SELECT id, action_type, scope_type, scope_key, target_id,
                       before_state, after_state, reason, evidence, created_at
                FROM flywheel_learning_events
                ORDER BY created_at DESC
                LIMIT 50
                """
            ).fetchall()
        return {"snapshots": rows, "count": len(rows), "learning_events": learning}

    def record_flywheel_learning_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO flywheel_learning_events(
                  action_type, scope_type, scope_key, target_id,
                  before_state, after_state, reason, evidence
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    event["action_type"], event.get("scope_type") or "global",
                    event.get("scope_key") or "global", event.get("target_id"),
                    json.dumps(event.get("before_state") or {}, ensure_ascii=False),
                    json.dumps(event.get("after_state") or {}, ensure_ascii=False),
                    event["reason"],
                    json.dumps(event.get("evidence") or {}, ensure_ascii=False),
                ),
            ).fetchone()

    def latest_flywheel_learning_event(
        self,
        *,
        action_type: str,
        target_id: int | None = None,
        window_days: int = 30,
    ) -> dict[str, Any] | None:
        days = max(1, min(int(window_days), 365))
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT *
                FROM flywheel_learning_events
                WHERE action_type = %s
                  AND (%s::bigint IS NULL OR target_id = %s)
                  AND created_at >= NOW() - (%s::text || ' days')::interval
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (action_type, target_id, target_id, days),
            ).fetchone()

    def set_outbound_experiment_winner(self, experiment_id: int, *, variant: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE outbound_experiments
                SET winner_variant = %s,
                    winner_selected_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND status = 'active'
                RETURNING *
                """,
                (variant.strip(), experiment_id),
            ).fetchone()

    def outbound_quality_dashboard(self, *, user: dict[str, Any]) -> dict[str, Any]:
        is_admin = user.get("role") == "admin"
        owner_id = None if is_admin else int(user["id"])
        owner_sql = "TRUE" if is_admin else "c.owner_user_id = %s"
        owner_params: tuple[Any, ...] = () if is_admin else (owner_id,)
        with self.db.connect() as conn:
            lead_quality = conn.execute(
                f"""
                SELECT
                  COUNT(*)::integer AS total,
                  COUNT(*) FILTER (WHERE c.icp_assessment->>'tier' = 'priority')::integer AS priority,
                  COUNT(*) FILTER (WHERE c.icp_assessment->>'qualified' = 'true')::integer AS qualified,
                  COUNT(*) FILTER (WHERE c.icp_assessment->>'tier' = 'review')::integer AS review,
                  COUNT(*) FILTER (WHERE c.icp_assessment->>'tier' = 'disqualified')::integer AS disqualified,
                  ROUND(COALESCE(AVG(NULLIF(c.icp_assessment->>'score', '')::numeric), 0), 1) AS average_score
                FROM contacts c
                WHERE {owner_sql}
                """,
                owner_params,
            ).fetchone()
            copy_quality = conn.execute(
                f"""
                SELECT
                  COUNT(d.id) FILTER (
                    WHERE d.quality_review->>'status' IS NOT NULL
                  )::integer AS reviewed,
                  COUNT(d.id) FILTER (WHERE d.quality_review->>'status' = 'ready')::integer AS ready,
                  COUNT(d.id) FILTER (WHERE d.quality_review->>'status' = 'revise')::integer AS revise,
                  COUNT(d.id) FILTER (WHERE d.quality_review->>'status' = 'blocked')::integer AS blocked,
                  ROUND(COALESCE(AVG(NULLIF(d.quality_review->>'score', '')::numeric), 0), 1) AS average_score
                FROM email_drafts d
                JOIN contacts c ON c.id = d.contact_id
                WHERE {owner_sql}
                """,
                owner_params,
            ).fetchone()
            sent_total = conn.execute(
                f"""
                SELECT COUNT(om.id)::integer AS total
                FROM outreach_messages om
                JOIN contacts c ON c.id = om.contact_id
                WHERE om.sent_at IS NOT NULL AND {owner_sql}
                """,
                owner_params,
            ).fetchone()
            replies = conn.execute(
                f"""
                SELECT
                  COUNT(i.id)::integer AS total,
                  COUNT(i.id) FILTER (
                    WHERE i.metadata->'reply_classification'->>'positive' = 'true'
                  )::integer AS positive,
                  COUNT(i.id) FILTER (
                    WHERE i.metadata->'reply_classification'->>'label' LIKE 'negative%%'
                  )::integer AS negative,
                  COUNT(i.id) FILTER (
                    WHERE i.metadata->'reply_classification'->>'label' = 'ooo'
                  )::integer AS ooo,
                  COUNT(i.id) FILTER (
                    WHERE i.metadata->'reply_classification'->>'label' = 'unsubscribe'
                  )::integer AS unsubscribe
                FROM interactions i
                JOIN contacts c ON c.id = i.contact_id
                WHERE i.interaction_type = 'email_reply' AND {owner_sql}
                """,
                owner_params,
            ).fetchone()
            feedback = conn.execute(
                """
                SELECT f.predicted_qualified, f.expected_qualified
                FROM icp_feedback f
                JOIN contacts c ON c.id = f.contact_id
                WHERE %s::bigint IS NULL OR c.owner_user_id = %s
                ORDER BY f.created_at DESC
                LIMIT 500
                """,
                (owner_id, owner_id),
            ).fetchall()
            experiments = conn.execute(
                """
                SELECT e.*,
                       COALESCE(jsonb_agg(
                         jsonb_build_object(
                           'name', d.experiment_variant,
                           'sent', COALESCE(m.sent, 0),
                           'delivered', COALESCE(m.delivered, 0),
                           'opened', COALESCE(m.opened, 0),
                           'replies', COALESCE(m.replies, 0),
                           'positive_replies', COALESCE(m.positive_replies, 0),
                           'bounced', COALESCE(m.bounced, 0),
                           'unsubscribed', COALESCE(m.unsubscribed, 0)
                         )
                       ) FILTER (WHERE d.experiment_variant IS NOT NULL), '[]'::jsonb) AS measured_variants
                FROM outbound_experiments e
                LEFT JOIN (
                  SELECT DISTINCT experiment_id, experiment_variant
                  FROM email_drafts
                  WHERE experiment_id IS NOT NULL
                ) d ON d.experiment_id = e.id
                LEFT JOIN LATERAL (
                  SELECT
                    COUNT(*) FILTER (WHERE om.sent_at IS NOT NULL)::integer AS sent,
                    COUNT(*) FILTER (WHERE om.delivered_at IS NOT NULL)::integer AS delivered,
                    COUNT(*) FILTER (WHERE om.opened_at IS NOT NULL)::integer AS opened,
                    COUNT(*) FILTER (WHERE om.replied_at IS NOT NULL)::integer AS replies,
                    COUNT(*) FILTER (
                      WHERE i.metadata->'reply_classification'->>'positive' = 'true'
                    )::integer AS positive_replies,
                    COUNT(*) FILTER (WHERE om.bounced_at IS NOT NULL)::integer AS bounced,
                    COUNT(*) FILTER (
                      WHERE i.metadata->'reply_classification'->>'label' = 'unsubscribe'
                    )::integer AS unsubscribed
                  FROM outreach_messages om
                  LEFT JOIN interactions i
                    ON i.contact_id = om.contact_id
                   AND i.interaction_type = 'email_reply'
                   AND i.occurred_at >= COALESCE(om.sent_at, om.created_at)
                  WHERE om.experiment_id = e.id
                    AND om.experiment_variant = d.experiment_variant
                ) m ON TRUE
                WHERE %s::bigint IS NULL OR e.owner_user_id = %s
                GROUP BY e.id
                ORDER BY e.created_at DESC
                LIMIT 20
                """,
                (owner_id, owner_id),
            ).fetchall()

        profile = self.get_active_icp_profile(owner_user_id=owner_id)
        experiment_rows = []
        for experiment in experiments:
            measured = experiment.get("measured_variants") or experiment.get("variants") or []
            experiment_rows.append({**experiment, "analysis": summarize_experiment(measured)})
        sent_count = int(sent_total.get("total") or 0)
        positive = int(replies.get("positive") or 0)
        return {
            "lead_quality": lead_quality,
            "copy_quality": copy_quality,
            "replies": {
                **replies,
                "positive_reply_rate": round(100 * positive / sent_count, 2) if sent_count else 0.0,
                "sent_total": sent_count,
            },
            "icp_profile": profile,
            "icp_calibration": calibration_summary(
                feedback,
                current_threshold=int(profile.get("qualified_threshold") or 70),
            ),
            "experiments": experiment_rows,
        }

    def _campaign_metric_rows(self, *, owner_user_id: int | None) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT m.*
                FROM campaign_metrics m
                JOIN campaigns c ON c.id = m.campaign_id
                WHERE %s::bigint IS NULL OR c.owner_user_id = %s
                """,
                (owner_user_id, owner_user_id),
            ).fetchall()

    def contact_detail(self, contact_id: int, *, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        contact = self.get_contact_for_user(contact_id, user) if user else self.get_contact(contact_id)
        if not contact:
            return None
        return {
            "contact": contact,
            "activities": self.list_lifecycle_activities(contact_id),
            "research": self.get_contact_research(contact_id),
            "draft": self.get_latest_email_draft(contact_id, user_id=int(user["id"]) if user else None),
            "feedback": self.contact_feedback_summary(contact_id),
        }

    def get_contact_research(self, contact_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT contact_id, summary, company_signals, person_signals, news_signals,
                       sources, provider, researched_at, expires_at
                FROM contact_research
                WHERE contact_id = %s
                """,
                (contact_id,),
            ).fetchone()

    def upsert_contact_research(
        self,
        contact_id: int,
        *,
        summary: str,
        company_signals: list[dict[str, Any]],
        person_signals: list[dict[str, Any]],
        news_signals: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        provider: str,
        expires_at: Any,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO contact_research(
                    contact_id, summary, company_signals, person_signals, news_signals,
                    sources, provider, researched_at, expires_at
                )
                VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, NOW(), %s)
                ON CONFLICT (contact_id) DO UPDATE
                SET summary = EXCLUDED.summary,
                    company_signals = EXCLUDED.company_signals,
                    person_signals = EXCLUDED.person_signals,
                    news_signals = EXCLUDED.news_signals,
                    sources = EXCLUDED.sources,
                    provider = EXCLUDED.provider,
                    researched_at = NOW(),
                    expires_at = EXCLUDED.expires_at
                RETURNING contact_id, summary, company_signals, person_signals, news_signals,
                          sources, provider, researched_at, expires_at
                """,
                (
                    contact_id,
                    summary,
                    json.dumps(company_signals, ensure_ascii=False),
                    json.dumps(person_signals, ensure_ascii=False),
                    json.dumps(news_signals, ensure_ascii=False),
                    json.dumps(sources, ensure_ascii=False),
                    provider,
                    expires_at,
                ),
            ).fetchone()

    def save_email_draft(
        self,
        contact_id: int,
        *,
        user_id: int | None,
        sequence_step: int,
        mode: str,
        subject: str,
        body: str,
        research_snapshot: dict[str, Any] | None = None,
        quality_review: dict[str, Any] | None = None,
        experiment_id: int | None = None,
        experiment_variant: str | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO email_drafts(
                    contact_id, user_id, sequence_step, mode, subject, body, research_snapshot,
                    quality_review, experiment_id, experiment_variant
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                RETURNING id, contact_id, user_id, sequence_step, mode, subject, body,
                          research_snapshot, quality_review, experiment_id, experiment_variant,
                          status, created_at, sent_at, approved_at, approved_by_user_id
                """,
                (
                    contact_id,
                    user_id,
                    sequence_step,
                    mode,
                    subject,
                    body,
                    json.dumps(research_snapshot or {}, ensure_ascii=False),
                    json.dumps(quality_review or {}, ensure_ascii=False),
                    experiment_id,
                    experiment_variant,
                ),
            ).fetchone()

    def get_latest_email_draft(self, contact_id: int, *, user_id: int | None = None) -> dict[str, Any] | None:
        clauses = ["contact_id = %s"]
        params: list[Any] = [contact_id]
        if user_id is not None:
            clauses.append("(user_id = %s OR user_id IS NULL)")
            params.append(user_id)
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT id, contact_id, user_id, sequence_step, mode, subject, body,
                       research_snapshot, quality_review, experiment_id, experiment_variant,
                       status, created_at, sent_at, approved_at, approved_by_user_id
                FROM email_drafts
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()

    def approve_latest_email_draft(self, contact_id: int, *, user_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            return conn.execute(
                """
                UPDATE email_drafts
                SET status = 'approved', approved_at = NOW(), approved_by_user_id = %s
                WHERE id = (
                    SELECT id FROM email_drafts
                    WHERE contact_id = %s AND user_id = %s AND status IN ('draft', 'approved')
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                )
                RETURNING id, contact_id, user_id, sequence_step, mode, subject, body,
                          research_snapshot, quality_review, experiment_id, experiment_variant,
                          status, created_at, sent_at, approved_at, approved_by_user_id
                """,
                (user_id, contact_id, user_id),
            ).fetchone()

    def mark_latest_email_draft_sent(self, contact_id: int, *, user_id: int | None = None) -> None:
        clauses = ["contact_id = %s", "status = 'approved'"]
        params: list[Any] = [contact_id]
        if user_id is not None:
            clauses.append("user_id = %s")
            params.append(user_id)
        with self.db.connect() as conn:
            conn.execute(
                f"""
                UPDATE email_drafts
                SET status = 'sent', sent_at = NOW()
                WHERE id = (
                    SELECT id FROM email_drafts
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                )
                """,
                tuple(params),
            )

    def contact_feedback_summary(self, contact_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FILTER (WHERE event_type = 'sent') AS sent,
                       COUNT(*) FILTER (WHERE event_type = 'delivered') AS delivered,
                       COUNT(*) FILTER (WHERE event_type = 'opened') AS opened,
                       COUNT(*) FILTER (WHERE event_type = 'clicked') AS clicked,
                       COUNT(*) FILTER (WHERE event_type = 'replied') AS replied,
                       COUNT(*) FILTER (WHERE event_type = 'bounced') AS bounced,
                       MAX(occurred_at) AS last_event_at,
                       (ARRAY_AGG(event_type::text ORDER BY occurred_at DESC))[1] AS last_event_type
                FROM email_events
                WHERE contact_id = %s
                """,
                (contact_id,),
            ).fetchone()
            return row or {}

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
                    notes = COALESCE(%s, notes),
                    sabcd_stage = CASE
                        WHEN %s IN ('signed', 'maintenance') THEN 'S'
                        WHEN %s IN ('business_plan', 'trial_order', 'agency_agreement', 'store_creation', 'store_visit', 'hq_visit') AND sabcd_stage <> 'S' THEN 'A'
                        WHEN %s IN ('conversation', 'meeting') AND sabcd_stage IN ('D', 'C') THEN 'B'
                        WHEN %s = 'replied' AND sabcd_stage = 'D' THEN 'C'
                        ELSE sabcd_stage
                    END
                WHERE id = %s
                """,
                (lifecycle_stage, content[:500], lifecycle_stage, lifecycle_stage, lifecycle_stage, lifecycle_stage, contact_id),
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
                    AND pool_type = 'private'
                    AND email_status = 'valid'
                    AND email IS NOT NULL
                    AND email NOT LIKE '%%*%%'
                    AND email LIKE '%%@%%'
                    AND lower(split_part(email, '@', 1)) NOT IN ('admin','billing','contact','hello','help','info','office','press','sales','support','team')
                    AND COALESCE(lead_score, 60) >= 50
                    AND COALESCE(job_title, '') !~* '(assistant|customer service|intern|reception|receptionist|support)'
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
                  AND c.pool_type = 'private'
                  AND c.email_status = 'valid'
                  AND c.email IS NOT NULL
                  AND c.email NOT LIKE '%%*%%'
                  AND c.email LIKE '%%@%%'
                  AND lower(split_part(c.email, '@', 1)) NOT IN ('admin','billing','contact','hello','help','info','office','press','sales','support','team')
                  AND COALESCE(c.lead_score, 60) >= 50
                  AND COALESCE(c.job_title, '') !~* '(assistant|customer service|intern|reception|receptionist|support)'
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
                  AND pool_type = 'private'
                  AND status IN ('queued', 'sent_1', 'sent_2')
                  AND email IS NOT NULL
                  AND email NOT LIKE '%%*%%'
                  AND email LIKE '%%@%%'
                  AND lower(split_part(email, '@', 1)) NOT IN ('admin','billing','contact','hello','help','info','office','press','sales','support','team')
                  AND COALESCE(lead_score, 60) >= 50
                  AND COALESCE(job_title, '') !~* '(assistant|customer service|intern|reception|receptionist|support)'
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
                  AND pool_type = 'private'
                  AND status IN ('queued', 'sent_1', 'sent_2')
                  AND email IS NOT NULL
                  AND email NOT LIKE '%%*%%'
                  AND email LIKE '%%@%%'
                  AND lower(split_part(email, '@', 1)) NOT IN ('admin','billing','contact','hello','help','info','office','press','sales','support','team')
                  AND COALESCE(lead_score, 60) >= 50
                  AND COALESCE(job_title, '') !~* '(assistant|customer service|intern|reception|receptionist|support)'
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

    def reserve_send_attempt(
        self,
        contact_id: int,
        step: int,
        *,
        user_id: int | None,
        provider: str,
        sender_email: str | None,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO outbound_send_attempts(
                    contact_id, sequence_step, user_id, provider, sender_email, idempotency_key, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'sending')
                ON CONFLICT (contact_id, sequence_step) DO NOTHING
                RETURNING id, contact_id, sequence_step, status, idempotency_key
                """,
                (contact_id, step, user_id, provider, sender_email, idempotency_key),
            ).fetchone()
            if row:
                return {**row, "reserved": True}
            existing = conn.execute(
                """
                SELECT id, contact_id, sequence_step, status, idempotency_key, message_id, error
                FROM outbound_send_attempts
                WHERE contact_id = %s AND sequence_step = %s
                """,
                (contact_id, step),
            ).fetchone()
            return {**existing, "reserved": False} if existing else None

    def finish_send_attempt(self, contact_id: int, step: int, *, message_id: str | None = None, error: str | None = None) -> None:
        status = "sent" if not error else "failed"
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE outbound_send_attempts
                SET status = %s, message_id = COALESCE(%s, message_id), error = %s, updated_at = NOW()
                WHERE contact_id = %s AND sequence_step = %s
                """,
                (status, message_id, error, contact_id, step),
            )

    def record_sent(self, contact_id: int, step: int, subject: str, message_id: str | None, metadata: dict[str, Any]) -> bool:
        private_days = _private_pool_days(self.db.config.raw)
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
                SET status = %s, sequence_step = %s, last_contacted_at = NOW(),
                    last_stage_changed_at = CASE WHEN sabcd_stage = 'D' THEN NOW() ELSE last_stage_changed_at END,
                    pool_expires_at = CASE WHEN pool_type = 'private' AND sabcd_stage = 'D' THEN NOW() + (%s::text || ' days')::interval ELSE pool_expires_at END,
                    sabcd_stage = CASE WHEN sabcd_stage = 'D' THEN 'C' ELSE sabcd_stage END
                WHERE id = %s
                """,
                (f"sent_{step}", step, private_days, contact_id),
            )
            return True

    def record_manual_sent(self, contact_id: int, step: int, subject: str, message_id: str | None, metadata: dict[str, Any]) -> bool:
        private_days = _private_pool_days(self.db.config.raw)
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
                    last_stage_changed_at = CASE WHEN sabcd_stage = 'D' THEN NOW() ELSE last_stage_changed_at END,
                    pool_expires_at = CASE WHEN pool_type = 'private' AND sabcd_stage = 'D' THEN NOW() + (%s::text || ' days')::interval ELSE pool_expires_at END,
                    sabcd_stage = CASE WHEN sabcd_stage = 'D' THEN 'C' ELSE sabcd_stage END,
                    status = CASE
                        WHEN %s <= 1 THEN 'sent_1'::contact_status
                        WHEN %s = 2 THEN 'sent_2'::contact_status
                        ELSE 'sent_3'::contact_status
                    END
                WHERE id = %s
                """,
                (step, private_days, step, step, contact_id),
            )
            return bool(row)

    def find_contact_id_by_message_id(self, message_id: str) -> int | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT contact_id
                FROM email_events
                WHERE message_id = %s
                ORDER BY occurred_at DESC, id DESC
                LIMIT 1
                """,
                (message_id,),
            ).fetchone()
            return int(row["contact_id"]) if row else None

    def find_contact_id_by_email(self, email: str) -> int | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM contacts
                WHERE lower(email) = lower(%s)
                ORDER BY last_contacted_at DESC NULLS LAST, created_at DESC, id DESC
                LIMIT 1
                """,
                (email,),
            ).fetchone()
            return int(row["id"]) if row else None

    def route_inbound_reply(self, contact_id: int, routed_user_id: int | None) -> dict[str, Any]:
        """Keep a reply with the current owner, or restore it to the signed sending user."""

        private_days = _private_pool_days(self.db.config.raw)
        with self.db.connect() as conn:
            contact = conn.execute(
                "SELECT owner_user_id, pool_type FROM contacts WHERE id = %s FOR UPDATE",
                (contact_id,),
            ).fetchone()
            if not contact:
                raise RuntimeError("inbound_contact_not_found")
            current_owner_id = contact.get("owner_user_id")
            valid_routed_user_id: int | None = None
            if routed_user_id:
                routed_user = conn.execute(
                    "SELECT id FROM sales_users WHERE id = %s AND active = TRUE",
                    (routed_user_id,),
                ).fetchone()
                if routed_user:
                    valid_routed_user_id = int(routed_user["id"])
            owner_user_id = int(current_owner_id) if current_owner_id else valid_routed_user_id
            if owner_user_id:
                result = conn.execute(
                    """
                    UPDATE contacts
                    SET owner_user_id = %s,
                        owner = COALESCE((SELECT display_name FROM sales_users WHERE id = %s), owner),
                        pool_type = 'private',
                        assignment_source = CASE WHEN owner_user_id IS NULL THEN 'inbound_reply' ELSE assignment_source END,
                        assigned_at = COALESCE(assigned_at, NOW()),
                        pool_expires_at = NOW() + (%s::text || ' days')::interval,
                        reply_assignment_pending = FALSE,
                        last_reply_at = NOW()
                    WHERE id = %s
                    RETURNING owner_user_id, pool_type, reply_assignment_pending, last_reply_at
                    """,
                    (owner_user_id, owner_user_id, private_days, contact_id),
                ).fetchone()
            else:
                result = conn.execute(
                    """
                    UPDATE contacts
                    SET pool_type = 'public',
                        reply_assignment_pending = TRUE,
                        last_reply_at = NOW()
                    WHERE id = %s
                    RETURNING owner_user_id, pool_type, reply_assignment_pending, last_reply_at
                    """,
                    (contact_id,),
                ).fetchone()
            return dict(result)

    def allow_legacy_tracking(self, contact_id: int, step: int | None = None) -> bool:
        """Keep links from pre-signature emails functional after migration 022."""

        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM email_events e
                JOIN schema_migrations m
                  ON m.version = '022_identity_research_pipeline.sql'
                WHERE e.contact_id = %s
                  AND e.event_type = 'sent'
                  AND e.occurred_at < m.applied_at
                  AND (%s::integer IS NULL OR e.sequence_step = %s)
                LIMIT 1
                """,
                (contact_id, step, step),
            ).fetchone()
            return bool(row)

    def mark_status(self, contact_id: int, status: str, *, notes: str | None = None) -> None:
        validate_status(status)
        private_days = _private_pool_days(self.db.config.raw)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE contacts
                SET status = %s,
                    replied_at = CASE WHEN %s = 'replied' THEN NOW() ELSE replied_at END,
                    last_stage_changed_at = CASE WHEN %s = 'replied' AND sabcd_stage = 'D' THEN NOW() ELSE last_stage_changed_at END,
                    pool_expires_at = CASE WHEN %s = 'replied' AND pool_type = 'private' AND sabcd_stage = 'D' THEN NOW() + (%s::text || ' days')::interval ELSE pool_expires_at END,
                    sabcd_stage = CASE WHEN %s = 'replied' AND sabcd_stage = 'D' THEN 'C' ELSE sabcd_stage END,
                    notes = COALESCE(%s, notes)
                WHERE id = %s
                """,
                (status, status, status, status, private_days, status, notes, contact_id),
            )

    def record_event(self, contact_id: int, event_type: str, payload: dict[str, Any]) -> None:
        terminal = {
            "replied": "replied",
            "bounce": "bounced",
            "bounced": "bounced",
            "failed": "bounced",
            "suppressed": "bounced",
            "complained": "unsubscribed",
            "unsubscribe": "unsubscribed",
            "unsubscribed": "unsubscribed",
        }
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
            reply_classification = (
                payload.get("reply_classification")
                if isinstance(payload, dict) and isinstance(payload.get("reply_classification"), dict)
                else {}
            )
            should_advance = event_type != "replied" or not reply_classification or bool(reply_classification.get("should_advance"))
            if event_type in terminal:
                delivery_reason = payload.get("delivery_reason") if isinstance(payload, dict) else None
                private_days = _private_pool_days(self.db.config.raw)
                reply_label = str(reply_classification.get("label") or "")
                effective_status = terminal[event_type]
                if event_type == "replied" and reply_label == "unsubscribe":
                    effective_status = "unsubscribed"
                elif event_type == "replied" and reply_label == "bounce":
                    effective_status = "bounced"
                conn.execute(
                    """
                    UPDATE contacts
                    SET status = %s,
                        replied_at = CASE WHEN %s = 'replied' THEN NOW() ELSE replied_at END,
                        last_stage_changed_at = CASE WHEN %s AND %s = 'replied' AND sabcd_stage = 'D' THEN NOW() ELSE last_stage_changed_at END,
                        pool_expires_at = CASE WHEN %s AND %s = 'replied' AND pool_type = 'private' AND sabcd_stage = 'D' THEN NOW() + (%s::text || ' days')::interval ELSE pool_expires_at END,
                        sabcd_stage = CASE WHEN %s AND %s = 'replied' AND sabcd_stage = 'D' THEN 'C' ELSE sabcd_stage END,
                        enrich_error = CASE
                            WHEN %s IN ('bounced', 'unsubscribed') THEN COALESCE(%s, enrich_error)
                            ELSE enrich_error
                        END
                    WHERE id = %s
                    """,
                    (
                        effective_status,
                        effective_status,
                        should_advance,
                        effective_status,
                        should_advance,
                        effective_status,
                        private_days,
                        should_advance,
                        effective_status,
                        effective_status,
                        delivery_reason,
                        contact_id,
                    ),
                )

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
            if row:
                return True
            if not external_id:
                return False
            existing = conn.execute(
                """
                SELECT processed_at
                FROM webhook_events
                WHERE provider = %s AND external_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (provider, external_id),
            ).fetchone()
            return bool(existing and existing["processed_at"] is None)

    def mark_webhook_delivery_processed(self, provider: str, external_id: str | None) -> None:
        if not external_id:
            return
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE webhook_events
                SET processed_at = COALESCE(processed_at, NOW())
                WHERE provider = %s AND external_id = %s
                """,
                (provider, external_id),
            )

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

    def blacklist_match(self, *, email: str | None, domain: str | None) -> dict[str, Any] | None:
        if not email and not domain:
            return None
        with self.db.connect() as conn:
            return conn.execute(
                """
                SELECT id, email, domain, reason, created_at
                FROM blacklist
                WHERE (%s::text IS NOT NULL AND LOWER(email) = LOWER(%s))
                   OR (%s::text IS NOT NULL AND LOWER(domain) = LOWER(%s))
                ORDER BY id DESC
                LIMIT 1
                """,
                (email, email, domain, domain),
            ).fetchone()

    def create_campaign(
        self,
        *,
        name: str,
        channel: str,
        region: str | None = None,
        product_line: str | None = None,
        owner_user_id: int | None = None,
        budget_amount: float | None = None,
        currency: str = "USD",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO campaigns(
                    name, channel, region, product_line, owner_user_id, budget_amount, currency, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    name.strip(),
                    channel.strip(),
                    region,
                    product_line,
                    owner_user_id,
                    budget_amount,
                    currency or "USD",
                    json.dumps(metadata or {}),
                ),
            ).fetchone()

    def refresh_campaign_metrics(self, campaign_id: int) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                WITH metric AS (
                  SELECT
                    COUNT(DISTINCT l.id)::integer AS leads_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE c.email_status = 'valid' AND c.email IS NOT NULL)::integer AS valid_contacts_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE e.event_type = 'sent')::integer AS sent_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE e.event_type = 'opened')::integer AS opened_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE e.event_type = 'replied' OR c.status = 'replied')::integer AS replied_count,
                    COUNT(DISTINCT c.id) FILTER (
                      WHERE i.metadata->'reply_classification'->>'positive' = 'true'
                    )::integer AS positive_replied_count,
                    COUNT(DISTINCT c.id) FILTER (
                      WHERE i.metadata->'reply_classification'->>'label' LIKE 'negative%%'
                    )::integer AS negative_replied_count,
                    COUNT(DISTINCT c.id) FILTER (
                      WHERE i.metadata->'reply_classification'->>'label' = 'ooo'
                    )::integer AS ooo_count,
                    COUNT(DISTINCT c.id) FILTER (
                      WHERE e.event_type = 'unsubscribed'
                         OR i.metadata->'reply_classification'->>'label' = 'unsubscribe'
                    )::integer AS unsubscribe_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE c.lifecycle_stage IN ('meeting', 'business_plan', 'store_visit', 'trial_order', 'agency_agreement', 'hq_visit', 'signed', 'maintenance'))::integer AS meeting_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE c.lifecycle_stage IN ('business_plan', 'trial_order', 'agency_agreement', 'signed', 'maintenance'))::integer AS quoted_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE c.lifecycle_stage IN ('signed', 'maintenance') OR c.disposition = 'won')::integer AS won_count,
                    COUNT(DISTINCT c.id) FILTER (WHERE c.lifecycle_stage = 'abandoned' OR c.disposition IN ('abandoned', 'lost'))::integer AS lost_count
                  FROM leads l
                  LEFT JOIN contacts c ON c.id = l.contact_id
                  LEFT JOIN email_events e ON e.contact_id = c.id
                  LEFT JOIN interactions i
                    ON i.contact_id = c.id AND i.interaction_type = 'email_reply'
                  WHERE l.campaign_id = %s
                )
                INSERT INTO campaign_metrics(
                    campaign_id, metric_date, leads_count, valid_contacts_count, sent_count,
                    opened_count, replied_count, positive_replied_count, negative_replied_count,
                    ooo_count, unsubscribe_count, meeting_count, quoted_count, won_count, lost_count
                )
                SELECT %s, CURRENT_DATE, leads_count, valid_contacts_count, sent_count,
                       opened_count, replied_count, positive_replied_count, negative_replied_count,
                       ooo_count, unsubscribe_count, meeting_count, quoted_count, won_count, lost_count
                FROM metric
                ON CONFLICT (campaign_id, metric_date)
                DO UPDATE SET
                    leads_count = EXCLUDED.leads_count,
                    valid_contacts_count = EXCLUDED.valid_contacts_count,
                    sent_count = EXCLUDED.sent_count,
                    opened_count = EXCLUDED.opened_count,
                    replied_count = EXCLUDED.replied_count,
                    positive_replied_count = EXCLUDED.positive_replied_count,
                    negative_replied_count = EXCLUDED.negative_replied_count,
                    ooo_count = EXCLUDED.ooo_count,
                    unsubscribe_count = EXCLUDED.unsubscribe_count,
                    meeting_count = EXCLUDED.meeting_count,
                    quoted_count = EXCLUDED.quoted_count,
                    won_count = EXCLUDED.won_count,
                    lost_count = EXCLUDED.lost_count,
                    updated_at = NOW()
                RETURNING *
                """,
                (campaign_id, campaign_id),
            ).fetchone()

    def refresh_contact_campaign_metrics(self, contact_id: int) -> int:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT campaign_id FROM leads WHERE contact_id = %s AND campaign_id IS NOT NULL",
                (contact_id,),
            ).fetchall()
        campaign_ids = [int(row["campaign_id"]) for row in rows]
        for campaign_id in campaign_ids:
            self.refresh_campaign_metrics(campaign_id)
        return len(campaign_ids)

    def upsert_lead_record(
        self,
        *,
        source_type: str,
        external_id: str | None = None,
        source_ref: str | None = None,
        source_row: int | None = None,
        campaign_id: int | None = None,
        contact_id: int | None = None,
        owner_user_id: int | None = None,
        raw_data: dict[str, Any] | None = None,
        normalized_email: str | None = None,
        normalized_phone: str | None = None,
        normalized_whatsapp: str | None = None,
        company_domain: str | None = None,
        country: str | None = None,
        region: str | None = None,
        language: str | None = None,
        dedupe_key: str | None = None,
        status: str = "new",
        quality_score: int | None = None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO leads(
                    external_id, source_type, source_ref, source_row, campaign_id, contact_id, owner_user_id,
                    raw_data, normalized_email, normalized_phone, normalized_whatsapp, company_domain,
                    country, region, language, dedupe_key, status, quality_score, failure_reason
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_type, external_id)
                DO UPDATE SET
                    source_ref = COALESCE(EXCLUDED.source_ref, leads.source_ref),
                    source_row = COALESCE(EXCLUDED.source_row, leads.source_row),
                    campaign_id = COALESCE(EXCLUDED.campaign_id, leads.campaign_id),
                    contact_id = COALESCE(EXCLUDED.contact_id, leads.contact_id),
                    owner_user_id = COALESCE(EXCLUDED.owner_user_id, leads.owner_user_id),
                    raw_data = leads.raw_data || EXCLUDED.raw_data,
                    normalized_email = COALESCE(EXCLUDED.normalized_email, leads.normalized_email),
                    normalized_phone = COALESCE(EXCLUDED.normalized_phone, leads.normalized_phone),
                    normalized_whatsapp = COALESCE(EXCLUDED.normalized_whatsapp, leads.normalized_whatsapp),
                    company_domain = COALESCE(EXCLUDED.company_domain, leads.company_domain),
                    country = COALESCE(EXCLUDED.country, leads.country),
                    region = COALESCE(EXCLUDED.region, leads.region),
                    language = COALESCE(EXCLUDED.language, leads.language),
                    dedupe_key = COALESCE(EXCLUDED.dedupe_key, leads.dedupe_key),
                    status = EXCLUDED.status,
                    quality_score = COALESCE(EXCLUDED.quality_score, leads.quality_score),
                    failure_reason = COALESCE(EXCLUDED.failure_reason, leads.failure_reason),
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    external_id,
                    source_type,
                    source_ref,
                    source_row,
                    campaign_id,
                    contact_id,
                    owner_user_id,
                    json.dumps(raw_data or {}),
                    _clean_optional_email(normalized_email),
                    normalized_phone,
                    normalized_whatsapp,
                    company_domain,
                    country,
                    region,
                    language,
                    dedupe_key,
                    status,
                    quality_score,
                    failure_reason,
                ),
            ).fetchone()

    def record_interaction(
        self,
        *,
        contact_id: int,
        interaction_type: str,
        channel: str,
        direction: str = "outbound",
        lead_id: int | None = None,
        user_id: int | None = None,
        subject: str | None = None,
        content: str | None = None,
        outcome: str | None = None,
        source_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO interactions(
                    contact_id, lead_id, user_id, interaction_type, direction, channel,
                    subject, content, outcome, source_ref, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    contact_id,
                    lead_id,
                    user_id,
                    interaction_type,
                    direction,
                    channel,
                    subject,
                    content,
                    outcome,
                    source_ref,
                    json.dumps(metadata or {}),
                ),
            ).fetchone()

    def create_followup_task(
        self,
        *,
        contact_id: int,
        assigned_user_id: int | None,
        title: str,
        task_type: str = "followup",
        priority: str = "normal",
        lead_id: int | None = None,
        created_by_user_id: int | None = None,
        description: str | None = None,
        due_at: str | None = None,
        trigger_rule: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            return conn.execute(
                """
                INSERT INTO followup_tasks(
                    contact_id, lead_id, assigned_user_id, created_by_user_id, task_type, priority,
                    title, description, due_at, trigger_rule, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s, %s::jsonb)
                RETURNING *
                """,
                (
                    contact_id,
                    lead_id,
                    assigned_user_id,
                    created_by_user_id,
                    task_type,
                    priority,
                    title,
                    description,
                    due_at,
                    trigger_rule,
                    json.dumps(metadata or {}),
                ),
            ).fetchone()

    def ensure_followup_task(
        self,
        *,
        contact_id: int,
        assigned_user_id: int | None,
        title: str,
        task_type: str = "followup",
        priority: str = "normal",
        lead_id: int | None = None,
        created_by_user_id: int | None = None,
        description: str | None = None,
        due_at: str | None = None,
        trigger_rule: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                INSERT INTO followup_tasks(
                    contact_id, lead_id, assigned_user_id, created_by_user_id, task_type, priority,
                    title, description, due_at, trigger_rule, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, %s, %s::jsonb)
                ON CONFLICT (contact_id, task_type, trigger_rule)
                  WHERE status = 'open' AND trigger_rule IS NOT NULL
                DO UPDATE SET
                    assigned_user_id = COALESCE(EXCLUDED.assigned_user_id, followup_tasks.assigned_user_id),
                    priority = EXCLUDED.priority,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    due_at = CASE
                        WHEN followup_tasks.due_at IS NULL THEN EXCLUDED.due_at
                        WHEN EXCLUDED.due_at IS NULL THEN followup_tasks.due_at
                        ELSE LEAST(followup_tasks.due_at, EXCLUDED.due_at)
                    END,
                    metadata = followup_tasks.metadata || EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    contact_id,
                    lead_id,
                    assigned_user_id,
                    created_by_user_id,
                    task_type,
                    priority,
                    title,
                    description,
                    due_at,
                    trigger_rule,
                    json.dumps(metadata or {}),
                ),
            ).fetchone()
            return dict(row) if row else None

    def list_followup_tasks(
        self,
        *,
        user: dict[str, Any],
        status: str = "open",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["t.status = %s"]
        params: list[Any] = [status]
        if user.get("role") != "admin":
            clauses.append("(t.assigned_user_id = %s OR (t.assigned_user_id IS NULL AND c.owner_user_id = %s))")
            params.extend([user["id"], user["id"]])
        params.append(max(1, min(int(limit), 500)))
        with self.db.connect() as conn:
            return conn.execute(
                f"""
                SELECT t.*, c.first_name, c.last_name, c.company_name, c.email, c.phone,
                       c.status::text AS contact_status, c.lifecycle_stage, c.sabcd_stage,
                       c.pool_type, c.owner_user_id, u.display_name AS assigned_user_name
                FROM followup_tasks t
                JOIN contacts c ON c.id = t.contact_id
                LEFT JOIN sales_users u ON u.id = t.assigned_user_id
                WHERE {' AND '.join(clauses)}
                ORDER BY
                  CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                  t.due_at NULLS LAST,
                  t.created_at
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()

    def complete_followup_task(
        self,
        task_id: int,
        *,
        user: dict[str, Any],
        outcome: str | None = None,
    ) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            if user.get("role") == "admin":
                row = conn.execute(
                    """
                    UPDATE followup_tasks
                    SET status = 'completed', completed_at = NOW(),
                        metadata = metadata || jsonb_build_object(
                            'outcome', COALESCE(%s, 'completed'), 'completed_by', %s
                        ),
                        updated_at = NOW()
                    WHERE id = %s
                      AND status = 'open'
                    RETURNING *
                    """,
                    (outcome, user["id"], task_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE followup_tasks t
                    SET status = 'completed', completed_at = NOW(),
                        metadata = t.metadata || jsonb_build_object(
                            'outcome', COALESCE(%s, 'completed'), 'completed_by', %s
                        ),
                        updated_at = NOW()
                    FROM contacts c
                    WHERE t.id = %s AND c.id = t.contact_id
                      AND t.status = 'open'
                      AND (t.assigned_user_id = %s OR (t.assigned_user_id IS NULL AND c.owner_user_id = %s))
                    RETURNING t.*
                    """,
                    (outcome, user["id"], task_id, user["id"], user["id"]),
                ).fetchone()
            if not row:
                return None
            task = dict(row)
            if task.get("task_type") == "call":
                conn.execute(
                    """
                    INSERT INTO interactions(
                        contact_id, lead_id, user_id, interaction_type, direction, channel,
                        subject, content, outcome, source_ref, metadata
                    )
                    VALUES (%s, %s, %s, 'phone_call', 'outbound', 'phone', %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        task["contact_id"],
                        task.get("lead_id"),
                        user["id"],
                        task.get("title"),
                        task.get("description"),
                        outcome or "completed",
                        f"followup_task:{task['id']}",
                        json.dumps({"task_id": task["id"]}),
                    ),
                )
            return task

    def close_open_followup_tasks(self, contact_id: int, *, except_trigger: str | None = None) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                UPDATE followup_tasks
                SET status = 'cancelled', completed_at = NOW(), updated_at = NOW()
                WHERE contact_id = %s AND status = 'open'
                  AND (%s::text IS NULL OR trigger_rule IS DISTINCT FROM %s)
                RETURNING id
                """,
                (contact_id, except_trigger, except_trigger),
            ).fetchall()
            return len(row)

    def record_outreach_message(
        self,
        *,
        contact_id: int,
        channel: str,
        body: str,
        user_id: int | None = None,
        lead_id: int | None = None,
        campaign_id: int | None = None,
        draft_id: int | None = None,
        sequence_step: int = 1,
        subject: str | None = None,
        language: str | None = None,
        ai_model: str | None = None,
        personalization_evidence: list[dict[str, Any]] | None = None,
        status: str = "draft",
        provider: str | None = None,
        provider_message_id: str | None = None,
        error: str | None = None,
        quality_review: dict[str, Any] | None = None,
        experiment_id: int | None = None,
        experiment_variant: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.db.connect() as conn:
            if lead_id is None or campaign_id is None:
                lead_context = conn.execute(
                    """
                    SELECT id, campaign_id
                    FROM leads
                    WHERE contact_id = %s
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (contact_id,),
                ).fetchone()
                if lead_context:
                    lead_id = lead_id or int(lead_context["id"])
                    campaign_id = campaign_id or lead_context.get("campaign_id")
            return conn.execute(
                """
                INSERT INTO outreach_messages(
                    contact_id, lead_id, campaign_id, user_id, draft_id, channel, sequence_step,
                    subject, body, language, ai_model, personalization_evidence, status, provider,
                    provider_message_id, error, quality_review, experiment_id, experiment_variant, metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                    %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb
                )
                ON CONFLICT (draft_id) WHERE draft_id IS NOT NULL
                DO UPDATE SET
                    lead_id = COALESCE(EXCLUDED.lead_id, outreach_messages.lead_id),
                    campaign_id = COALESCE(EXCLUDED.campaign_id, outreach_messages.campaign_id),
                    subject = EXCLUDED.subject,
                    body = EXCLUDED.body,
                    language = COALESCE(EXCLUDED.language, outreach_messages.language),
                    ai_model = COALESCE(EXCLUDED.ai_model, outreach_messages.ai_model),
                    personalization_evidence = EXCLUDED.personalization_evidence,
                    status = EXCLUDED.status,
                    provider = COALESCE(EXCLUDED.provider, outreach_messages.provider),
                    provider_message_id = COALESCE(EXCLUDED.provider_message_id, outreach_messages.provider_message_id),
                    error = EXCLUDED.error,
                    quality_review = EXCLUDED.quality_review,
                    experiment_id = COALESCE(EXCLUDED.experiment_id, outreach_messages.experiment_id),
                    experiment_variant = COALESCE(EXCLUDED.experiment_variant, outreach_messages.experiment_variant),
                    metadata = outreach_messages.metadata || EXCLUDED.metadata,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    contact_id,
                    lead_id,
                    campaign_id,
                    user_id,
                    draft_id,
                    channel,
                    sequence_step,
                    subject,
                    body,
                    language,
                    ai_model,
                    json.dumps(personalization_evidence or []),
                    status,
                    provider,
                    provider_message_id,
                    error,
                    json.dumps(quality_review or {}),
                    experiment_id,
                    experiment_variant,
                    json.dumps(metadata or {}),
                ),
            ).fetchone()

    def update_outreach_message_event(
        self,
        *,
        provider: str,
        provider_message_id: str,
        event_type: str,
        error: str | None = None,
    ) -> None:
        event_columns = {
            "sent": "sent_at",
            "delivered": "delivered_at",
            "opened": "opened_at",
            "clicked": "opened_at",
            "replied": "replied_at",
            "bounced": "bounced_at",
            "bounce": "bounced_at",
            "failed": "bounced_at",
        }
        column = event_columns.get(event_type)
        if not column:
            return
        status = {
            "bounce": "bounced",
            "failed": "bounced",
            "clicked": "opened",
        }.get(event_type, event_type)
        with self.db.connect() as conn:
            row = conn.execute(
                f"""
                UPDATE outreach_messages
                SET status = %s,
                    {column} = COALESCE({column}, NOW()),
                    error = COALESCE(%s, error),
                    updated_at = NOW()
                WHERE provider = %s AND provider_message_id = %s
                RETURNING campaign_id
                """,
                (status, error, provider, provider_message_id),
            ).fetchone()
        if row and row.get("campaign_id"):
            self.refresh_campaign_metrics(int(row["campaign_id"]))

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


def _merge_contact_candidates(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    status_rank = {"valid": 5, "accept_all": 4, "risky": 3, "unknown": 2, "unverified": 1}
    merged: dict[str, dict[str, Any]] = {}
    for item in [*existing, *incoming]:
        value = str(item.get(key) or "").strip().lower()
        if not value:
            continue
        current = merged.get(value)
        better = not current or (
            status_rank.get(str(item.get("status") or ""), 0), int(item.get("confidence") or 0)
        ) > (
            status_rank.get(str(current.get("status") or ""), 0), int(current.get("confidence") or 0)
        )
        if better:
            merged[value] = {**(current or {}), **item}
    return list(merged.values())


def _contact_defaults(contact: dict[str, Any]) -> dict[str, Any]:
    return {
        "linkedin_url": _contact_identity_url(contact),
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
        "source_context": contact.get("source_context") or {},
        "identity_confidence": contact.get("identity_confidence"),
        "identity_status": contact.get("identity_status"),
        "identity_evidence": contact.get("identity_evidence") or [],
    }


def _contact_identity_url(contact: dict[str, Any]) -> str:
    linkedin_url = str(contact.get("linkedin_url") or "").strip()
    if linkedin_url:
        return linkedin_url
    identity = "|".join(
        str(contact.get(field) or "").strip().lower()
        for field in ("email", "first_name", "last_name", "company_domain", "company_name", "source_person_id")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"urn:contact:{digest}"


def _with_customer_intelligence(contact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not contact:
        return contact
    insights = contact.get("profile_insights")
    if not isinstance(insights, dict):
        insights = {}
    if not insights.get("icp_fit_score"):
        fallback = build_customer_profile(contact)
        insights = {**fallback, **insights}
        contact = {**contact, "profile_insights": insights}
        if not contact.get("profile_summary"):
            contact["profile_summary"] = fallback["summary"]
    if not isinstance(contact.get("icp_assessment"), dict) or not contact.get("icp_assessment"):
        contact = {**contact, "icp_assessment": assess_icp(contact)}
    return contact


def _normalize_pool_type(value: Any, owner_user_id: Any = None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"public", "private"}:
        return normalized
    return "private" if owner_user_id else "public"


def _private_pool_days(raw_config: dict[str, Any]) -> int:
    try:
        return max(1, int(raw_config.get("customer_pool", {}).get("private_pool_days") or 60))
    except Exception:
        return 60


def _region_assignment_rules(raw_config: dict[str, Any]) -> list[dict[str, Any]]:
    rules = raw_config.get("customer_pool", {}).get("region_assignments") or []
    if isinstance(rules, list):
        return [rule for rule in rules if isinstance(rule, dict) and rule.get("owner")]
    return []


def _match_region_owner(contact: dict[str, Any], rules: list[dict[str, Any]], users: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    context = contact.get("source_context") if isinstance(contact.get("source_context"), dict) else {}
    haystack = " ".join(
        str(item or "").lower()
        for item in (
            contact.get("location"),
            contact.get("company_name"),
            context.get("seed_location"),
            context.get("seed_category"),
            context.get("seed_reason"),
        )
    )
    for rule in rules:
        matches = rule.get("match") or rule.get("regions") or rule.get("countries") or []
        if isinstance(matches, str):
            matches = [matches]
        if not matches or any(str(item or "").lower() in haystack for item in matches):
            return users.get(str(rule.get("owner") or "").lower())
    return None


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


def _clean_optional_email(value: str | None) -> str | None:
    email = str(value or "").strip()
    if not email:
        return None
    if "@" not in email or " " in email:
        raise ValueError("invalid_reply_to_email")
    return email


def _clean_optional_alias(value: str | None) -> str | None:
    alias = str(value or "").strip().lower()
    if not alias:
        return None
    if len(alias) > 48 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", alias):
        raise ValueError("invalid_sender_alias_localpart")
    return alias


def _blank_to_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _vps_username(username: str, odoo_user_id: int) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.@-]", "_", str(username or "").strip().lower())
    if normalized:
        return normalized[:80]
    return f"odoo_{odoo_user_id}"
