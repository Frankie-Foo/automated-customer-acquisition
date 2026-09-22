"""Batch audit views over the existing lead, message and lifecycle records."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


# A contact's old replies are not outcomes of every campaign it later joins.
_MESSAGE_COUNTS = """
    COUNT(*) FILTER (WHERE m.sent_at IS NOT NULL) AS sent_count,
    COUNT(*) FILTER (WHERE m.replied_at IS NOT NULL AND m.sent_at IS NOT NULL) AS replied_count,
    COUNT(*) FILTER (WHERE m.opened_at IS NOT NULL AND m.sent_at IS NOT NULL) AS opened_count,
    COUNT(*) FILTER (WHERE m.status = 'failed') AS failed_count,
    MAX(m.sent_at) AS last_sent_at
"""
_REAL_MESSAGE = "m.channel = 'email' AND COALESCE(m.metadata->>'dry_run', 'false') <> 'true'"


def _actor(user: dict[str, Any] | None) -> tuple[int, bool]:
    if not user or not user.get("id") or user.get("role") not in {"sales", "admin"}:
        raise PermissionError("Authentication required")
    return int(user["id"]), user["role"] == "admin"


class OutreachBatchService:
    def __init__(self, repo):
        self.repo = repo

    def create(self, *, user, name, contact_ids, source_ref="", source_rows=None, metadata=None):
        """Register owned contacts without sending, changing owners, or calling a model."""
        user_id, _ = _actor(user)
        name = str(name or "").strip()
        if not name or len(name) > 200:
            raise ValueError("Batch name must contain 1-200 characters")
        if not isinstance(contact_ids, list) or not 1 <= len(contact_ids) <= 1000:
            raise ValueError("Select 1-1000 contacts")
        if any(type(value) is not int or value <= 0 for value in contact_ids):
            raise ValueError("Contact IDs must be positive integers")
        ids = list(dict.fromkeys(contact_ids))
        source_ref = str(source_ref or "").strip()[:500]
        key = hashlib.sha256(json.dumps([user_id, name, source_ref, sorted(ids)]).encode()).hexdigest()
        original_rows = source_rows or {}
        with self.repo.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contacts WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (ids,)
            ).fetchall()
            if len(rows) != len(ids) or any(
                row.get("pool_type") != "private" or row.get("owner_user_id") != user_id for row in rows
            ):
                raise PermissionError("All batch contacts must belong to the selected salesperson")
            campaign = conn.execute(
                """INSERT INTO campaigns(name, channel, owner_user_id, idempotency_key, metadata)
                   VALUES (%s, 'outreach_batch', %s, %s, %s::jsonb)
                   ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
                   DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key RETURNING *""",
                (name, user_id, f"outreach:{key}", json.dumps({
                    **(metadata or {}), "source_ref": source_ref, "workflow_version": 1,
                    "auto_send": False,
                }, ensure_ascii=False)),
            ).fetchone()
            by_id = {row["id"]: row for row in rows}
            for index, contact_id in enumerate(ids, 1):
                contact = by_id[contact_id]
                snapshot = {key: contact.get(key) for key in (
                    "id", "first_name", "last_name", "company_name", "company_domain", "job_title",
                    "email", "email_status", "phone", "linkedin_url", "location", "industry",
                    "source", "source_context", "owner_user_id", "pool_type",
                )}
                conn.execute(
                    """INSERT INTO leads(external_id, source_type, source_ref, source_row,
                           campaign_id, contact_id, owner_user_id, raw_data, normalized_email, status)
                       VALUES (%s, 'outreach_batch', %s, %s, %s, %s, %s, %s::jsonb, %s, 'promoted')
                       ON CONFLICT (source_type, external_id) DO NOTHING""",
                    (f"{campaign['id']}:{contact_id}", source_ref, index, campaign["id"], contact_id,
                     user_id, json.dumps({"contact_snapshot": snapshot,
                         "original_row": original_rows.get(str(contact_id), {})},
                         ensure_ascii=False, default=str), contact.get("email")),
                )
            conn.execute(
                """UPDATE leads SET automation_status='pending', automation_updated_at=NOW()
                   WHERE campaign_id=%s AND source_type='outreach_batch' AND automation_status IS NULL""",
                (campaign["id"],),
            )
        return {"batch": campaign, "total": len(ids)}

    def list(self, *, user):
        user_id, admin = _actor(user)
        with self.repo.db.connect() as conn:
            return conn.execute(
                f"""SELECT b.id, b.name, b.region, b.created_at,
                       COALESCE(u.display_name, u.username) AS owner_name,
                       b.automation_status, b.automation_config, b.automation_error,
                       b.automation_started_at, b.automation_completed_at, b.automation_updated_at,
                       b.metadata->>'source_ref' AS source_ref,
                       (SELECT COUNT(DISTINCT c.id) FROM leads l JOIN contacts c ON c.id=l.contact_id
                        WHERE l.campaign_id=b.id AND (%s OR c.owner_user_id=%s)) AS total_count,
                       (SELECT COUNT(*) FROM outreach_messages m JOIN contacts c ON c.id=m.contact_id
                        WHERE m.campaign_id=b.id AND m.sent_at IS NOT NULL AND {_REAL_MESSAGE}
                          AND (%s OR (m.user_id=%s AND c.owner_user_id=%s))) AS sent_messages,
                       (SELECT COUNT(DISTINCT m.contact_id) FROM outreach_messages m JOIN contacts c ON c.id=m.contact_id
                        WHERE m.campaign_id=b.id AND m.sent_at IS NOT NULL AND {_REAL_MESSAGE}
                          AND (%s OR (m.user_id=%s AND c.owner_user_id=%s))) AS sent_contacts,
                       (SELECT COUNT(DISTINCT m.contact_id) FROM outreach_messages m JOIN contacts c ON c.id=m.contact_id
                        WHERE m.campaign_id=b.id AND m.sent_at IS NOT NULL AND m.replied_at IS NOT NULL
                          AND {_REAL_MESSAGE} AND (%s OR (m.user_id=%s AND c.owner_user_id=%s))) AS replied_contacts
                    FROM campaigns b LEFT JOIN sales_users u ON u.id=b.owner_user_id
                    WHERE (%s OR b.owner_user_id=%s)
                    ORDER BY b.created_at DESC, b.id DESC""",
                (admin, user_id, admin, user_id, user_id, admin, user_id, user_id,
                 admin, user_id, user_id, admin, user_id),
            ).fetchall()

    def detail(self, batch_id, *, user, limit=25, offset=0, search=""):
        user_id, admin = _actor(user)
        limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
        with self.repo.db.connect() as conn:
            batch = conn.execute(
                """SELECT b.id, b.name, b.region, b.created_at, b.owner_user_id,
                          b.automation_status, b.automation_config, b.automation_error,
                          b.automation_started_at, b.automation_completed_at, b.automation_updated_at,
                          b.metadata->>'source_ref' AS source_ref,
                          COALESCE(u.display_name, u.username) AS owner_name
                   FROM campaigns b LEFT JOIN sales_users u ON u.id=b.owner_user_id
                   WHERE b.id=%s AND (%s OR b.owner_user_id=%s)""",
                (int(batch_id), admin, user_id),
            ).fetchone()
            if not batch:
                raise PermissionError("Batch not found or not accessible")
            rows = conn.execute(
                f"""SELECT c.id, c.first_name, c.last_name, c.company_name, c.job_title, c.email,
                          c.status, c.lifecycle_stage, c.profile_summary, c.owner_user_id,
                          COALESCE(u.display_name,u.username) AS owner_name,
                          l.source_row, l.source_ref, l.raw_data,
                          l.automation_status, l.automation_reason, l.automation_attempts,
                          l.automation_updated_at,
                          r.summary AS research_summary, r.sources AS research_sources,
                          r.researched_at, m.subject AS latest_subject, m.body AS latest_body,
                          CASE WHEN m.status = 'draft' AND d.status = 'approved'
                               THEN d.status ELSE m.status END AS last_message_status,
                          m.quality_review,
                          m.personalization_evidence, m.ai_model, m.metadata->>'sender_email' AS sender_email,
                          m.metadata->>'recipient_email' AS recipient_email,
                          m.metadata->'cc_emails' AS cc_emails,
                          counts.sent_count, counts.replied_count, counts.opened_count,
                          counts.failed_count, counts.last_sent_at,
                          task.title AS next_task_title, task.due_at AS next_task_due_at,
                          CASE WHEN c.owner_user_id IS DISTINCT FROM b.owner_user_id THEN 'ownership_changed'
                               WHEN c.status::text IN ('unsubscribed','bounced','complained','blocked') THEN c.status::text
                               WHEN COALESCE(c.email,'') = '' OR COALESCE(c.email_status,'') <> 'valid' THEN 'email_unverified'
                               ELSE NULL END AS eligibility_reason
                   FROM campaigns b
                   JOIN (SELECT DISTINCT ON (contact_id) * FROM leads WHERE campaign_id=%s
                         ORDER BY contact_id, id DESC) l ON l.campaign_id=b.id
                   JOIN contacts c ON c.id=l.contact_id
                   LEFT JOIN sales_users u ON u.id=c.owner_user_id
                   LEFT JOIN contact_research r ON r.contact_id=c.id
                   LEFT JOIN LATERAL (
                       SELECT * FROM outreach_messages m WHERE m.campaign_id=b.id AND m.contact_id=c.id
                       AND (%s OR m.user_id=%s) AND {_REAL_MESSAGE}
                       ORDER BY m.created_at DESC,m.id DESC LIMIT 1
                   ) m ON TRUE
                   LEFT JOIN email_drafts d ON d.id=m.draft_id AND d.contact_id=c.id
                   LEFT JOIN LATERAL (
                       SELECT {_MESSAGE_COUNTS} FROM outreach_messages m
                       WHERE m.campaign_id=b.id AND m.contact_id=c.id AND {_REAL_MESSAGE}
                         AND (%s OR m.user_id=%s)
                   ) counts ON TRUE
                   LEFT JOIN LATERAL (
                       SELECT title,due_at FROM followup_tasks WHERE contact_id=c.id AND status='open'
                       AND (%s OR assigned_user_id=%s)
                       ORDER BY due_at ASC NULLS LAST,id LIMIT 1
                   ) task ON TRUE
                   WHERE b.id=%s AND (%s OR c.owner_user_id=%s)
                   ORDER BY l.source_row NULLS LAST,c.id""",
                (int(batch_id), admin, user_id, admin, user_id, admin, user_id,
                 int(batch_id), admin, user_id),
            ).fetchall()
        summary = summarize(rows)
        term = str(search or "").strip().casefold()
        if term:
            rows = [row for row in rows if term in " ".join(str(row.get(key) or "") for key in (
                "first_name", "last_name", "email", "company_name", "job_title", "latest_subject"
            )).casefold()]
        return {"batch": batch, "summary": summary, "total": len(rows),
                "contacts": rows[offset:offset + limit]}

    def configure_automation(self, batch_id: int, *, user, action: str, audit_cc=None):
        user_id, admin = _actor(user)
        action = str(action or "").strip().lower()
        if action not in {"start", "pause", "resume"}:
            raise ValueError("Action must be start, pause, or resume")
        cc = _email_list(audit_cc)
        with self.repo.db.connect() as conn:
            batch = conn.execute(
                """SELECT * FROM campaigns
                   WHERE id=%s AND channel='outreach_batch' AND (%s OR owner_user_id=%s)
                   FOR UPDATE""",
                (int(batch_id), admin, user_id),
            ).fetchone()
            if not batch:
                raise PermissionError("Batch not found or not accessible")
            if action == "pause":
                conn.execute(
                    """UPDATE campaigns SET automation_status='paused', automation_updated_at=NOW()
                       WHERE id=%s""",
                    (int(batch_id),),
                )
            else:
                config = batch.get("automation_config") if isinstance(batch.get("automation_config"), dict) else {}
                if cc:
                    config = {**config, "audit_cc": cc}
                if not _email_list(config.get("audit_cc")):
                    raise ValueError("At least one audit CC address is required")
                conn.execute(
                    """UPDATE campaigns
                       SET automation_status='running', automation_config=%s::jsonb,
                           automation_error=NULL, automation_started_at=COALESCE(automation_started_at, NOW()),
                           automation_completed_at=NULL, automation_updated_at=NOW()
                       WHERE id=%s""",
                    (json.dumps(config, ensure_ascii=False), int(batch_id)),
                )
                conn.execute(
                    """UPDATE leads l SET automation_status = CASE
                             WHEN EXISTS (SELECT 1 FROM outreach_messages m
                                          WHERE m.campaign_id=l.campaign_id AND m.contact_id=l.contact_id
                                            AND m.sent_at IS NOT NULL
                                            AND COALESCE(m.metadata->>'dry_run','false') <> 'true')
                             THEN 'sent' ELSE 'pending' END,
                           automation_reason=NULL, automation_updated_at=NOW()
                       WHERE l.campaign_id=%s AND l.source_type='outreach_batch'
                         AND (l.automation_status IS NULL OR l.automation_status IN ('failed','retry'))""",
                    (int(batch_id),),
                )
        return self.automation_status(int(batch_id), user=user)

    def automation_status(self, batch_id: int, *, user):
        user_id, admin = _actor(user)
        with self.repo.db.connect() as conn:
            row = conn.execute(
                """SELECT b.id, b.automation_status, b.automation_config, b.automation_error,
                          b.automation_started_at, b.automation_completed_at, b.automation_updated_at,
                          COUNT(l.id) AS total,
                          COUNT(*) FILTER (WHERE l.automation_status='pending') AS pending,
                          COUNT(*) FILTER (WHERE l.automation_status IN ('researching','sending')) AS active,
                          COUNT(*) FILTER (WHERE l.automation_status='ready') AS ready,
                          COUNT(*) FILTER (WHERE l.automation_status='sent') AS sent,
                          COUNT(*) FILTER (WHERE l.automation_status='held') AS held,
                          COUNT(*) FILTER (WHERE l.automation_status='retry') AS retry,
                          COUNT(*) FILTER (WHERE l.automation_status='failed') AS failed
                   FROM campaigns b LEFT JOIN leads l ON l.campaign_id=b.id AND l.source_type='outreach_batch'
                   WHERE b.id=%s AND b.channel='outreach_batch' AND (%s OR b.owner_user_id=%s)
                   GROUP BY b.id""",
                (int(batch_id), admin, user_id),
            ).fetchone()
        if not row:
            raise PermissionError("Batch not found or not accessible")
        return row


def summarize(rows):
    return {
        "total_count": len(rows),
        "profiled_contacts": sum(bool(row.get("researched_at")) for row in rows),
        "drafted_contacts": sum(bool(row.get("latest_subject")) for row in rows),
        "sent_contacts": sum(bool(row.get("sent_count")) for row in rows),
        "sent_messages": sum(int(row.get("sent_count") or 0) for row in rows),
        "replied_contacts": sum(bool(row.get("replied_count")) for row in rows),
        "failed_messages": sum(int(row.get("failed_count") or 0) for row in rows),
        "blocked_contacts": sum(bool(row.get("eligibility_reason")) for row in rows),
        "automation_pending": sum(row.get("automation_status") == "pending" for row in rows),
        "automation_active": sum(row.get("automation_status") in {"researching", "sending"} for row in rows),
        "automation_ready": sum(row.get("automation_status") == "ready" for row in rows),
        "automation_sent": sum(row.get("automation_status") == "sent" for row in rows),
        "automation_held": sum(row.get("automation_status") == "held" for row in rows),
        "automation_retry": sum(row.get("automation_status") == "retry" for row in rows),
        "automation_failed": sum(row.get("automation_status") == "failed" for row in rows),
    }


def _email_list(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("Audit CC must be a list of email addresses")
    result: list[str] = []
    for value in values:
        email = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", email):
            raise ValueError("Audit CC contains an invalid email address")
        if email.casefold() not in {item.casefold() for item in result}:
            result.append(email)
    return result
