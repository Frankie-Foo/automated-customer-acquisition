from __future__ import annotations

import argparse
import json
import mimetypes
import urllib.parse
from importlib import resources
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .auth import clear_session_cookie, default_admin_credentials, parse_session_cookie, public_user, session_cookie
from .clients import SlackClient
from .config import load_config
from .db import Database, Repository
from .health import check_database, check_readiness
from .importers import parse_company_seed_csv, parse_contacts_csv
from .linkedin_public_search import LinkedInPublicSearchService
from .quotas import QuotaService
from .sender_pool import SenderPoolManager
from .services import EnrichmentService, LifecycleService, OutreachService, PersonalizedEmailService, ProfileAgentService, QueueService, SchedulerService, SocialEnrichmentService, SourcingService, StageAgentService, WebhookService


def normalize_company_website(value: str | None) -> str:
    if not value:
        return ""
    from .clients import _domain_from_website

    return _domain_from_website(value) or ""

STATIC_DIR = Path(__file__).parent / "web_static"
MAX_JSON_BODY_BYTES = 5 * 1024 * 1024


def static_path(name: str) -> Path:
    decoded = urllib.parse.unquote(name).replace("\\", "/")
    parts = [part for part in decoded.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("invalid static path")
    return Path(str(resources.files("sales_automation").joinpath("web_static", *parts)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="salesbot-web")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    repo = Repository(Database(config))
    try:
        repo.db.migrate()
        repo.ensure_default_admin(*default_admin_credentials())
    except Exception as exc:
        print(f"Warning: database initialization skipped: {exc}")
    handler = make_handler(config, repo)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


def make_handler(config, repo: Repository):
    public_base_url = str(config.raw.get("app", {}).get("public_base_url") or "")
    secure_cookie = public_base_url.startswith("https://")

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_file(static_path("index.html"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/live":
                self._send_json({"ok": True, "data": {"status": "live"}})
                return
            if parsed.path.startswith("/static/"):
                try:
                    self._send_file(static_path(parsed.path.removeprefix("/static/")))
                except ValueError:
                    self.send_error(404)
                return
            if parsed.path == "/api/health":
                health = check_readiness(config, repo)
                self._send_json({"ok": health["database"]["ok"], "data": health}, status=200 if health["database"]["ok"] else 503)
                return
            if parsed.path in {"/unsubscribe", "/track/open"}:
                pass
            elif parsed.path.startswith("/api/") and not self._require_database():
                return
            if parsed.path == "/api/me":
                user = self._current_user()
                if not user:
                    self._send_json({"ok": False, "error": "unauthorized"}, status=401)
                    return
                quota_snapshot = QuotaService(config, repo).snapshot(user)
                self._send_json({"ok": True, "data": {"user": public_user(user), "usage": quota_snapshot["user_usage"], "quotas": quota_snapshot}})
                return
            if parsed.path == "/api/logout":
                token = parse_session_cookie(self.headers.get("Cookie"))
                repo.delete_session(token)
                self._send_json({"ok": True, "data": {"ok": True}}, headers={"Set-Cookie": clear_session_cookie(secure=secure_cookie)})
                return
            if parsed.path.startswith("/api/") and not self._require_user():
                return
            if parsed.path == "/api/summary":
                self._json(lambda: repo.dashboard_summary(user=self._current_user()))
                return
            if parsed.path == "/api/readiness":
                self._json(lambda: check_readiness(config, repo))
                return
            if parsed.path == "/unsubscribe":
                qs = parse_qs(parsed.query)
                contact_id = int(qs.get("contact_id", ["0"])[0])
                if contact_id:
                    repo.record_event(contact_id, "unsubscribed", {"source": "unsubscribe_link"})
                self._send_html("<!doctype html><title>Unsubscribed</title><p>You have been unsubscribed.</p>")
                return
            if parsed.path == "/track/open":
                qs = parse_qs(parsed.query)
                contact_id = int(qs.get("contact_id", ["0"])[0])
                step = int(qs.get("step", ["0"])[0])
                if contact_id:
                    repo.record_event(contact_id, "opened", {"source": "tracking_pixel", "step": step})
                self._send_pixel()
                return
            if parsed.path == "/api/contacts":
                qs = parse_qs(parsed.query)
                status = qs.get("status", [""])[0] or None
                search = qs.get("search", [""])[0] or None
                filter_key = qs.get("filter", [""])[0] or None
                limit = int(qs.get("limit", ["100"])[0])
                self._json(lambda: {"contacts": repo.list_contacts(status=status, search=search, filter_key=filter_key, limit=limit, user=self._current_user())})
                return
            if parsed.path == "/api/lifecycle":
                self._json(lambda: repo.lifecycle_summary(user=self._current_user()))
                return
            if parsed.path == "/api/ops-report":
                self._json(lambda: repo.operations_report(user=self._current_user()))
                return
            if parsed.path == "/api/search-tasks":
                self._json(lambda: {"tasks": repo.list_lead_search_tasks(user=self._current_user())})
                return
            if parsed.path == "/api/search-results":
                qs = parse_qs(parsed.query)
                task_id = int(qs.get("task_id", ["0"])[0])
                self._json(lambda: {"results": repo.list_lead_search_results(task_id, user=self._current_user())})
                return
            if parsed.path == "/api/admin/users":
                admin = self._require_admin()
                if not admin:
                    return
                self._json(lambda: {"users": repo.list_users()})
                return
            if parsed.path == "/api/admin/senders":
                admin = self._require_admin()
                if not admin:
                    return
                def senders() -> dict[str, Any]:
                    SenderPoolManager(config, repo).sync_accounts()
                    return {"senders": repo.list_sender_accounts()}
                self._json(senders)
                return
            if parsed.path == "/api/contact-detail":
                qs = parse_qs(parsed.query)
                contact_id = int(qs.get("contact_id", ["0"])[0])
                self._json(lambda: repo.contact_detail(contact_id, user=self._current_user()) or {})
                return
            if parsed.path == "/api/export.csv":
                qs = parse_qs(parsed.query)
                status = qs.get("status", [""])[0] or None
                self._send_csv(repo.export_contacts_csv_text(status=status, user=self._current_user()))
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=413 if "too_large" in str(exc) else 400)
                return
            if parsed.path == "/api/login":
                if not self._require_database():
                    return
                user = repo.authenticate_user(payload.get("username", ""), payload.get("password", ""))
                if not user:
                    self._send_json({"ok": False, "error": "用户名或密码错误"}, status=401)
                    return
                token = repo.create_session(int(user["id"]))
                quota_snapshot = QuotaService(config, repo).snapshot(user)
                self._send_json(
                    {"ok": True, "data": {"user": public_user(user), "usage": quota_snapshot["user_usage"], "quotas": quota_snapshot}},
                    headers={"Set-Cookie": session_cookie(token, secure=secure_cookie)},
                )
                return
            if parsed.path.startswith("/webhooks/"):
                pass
            elif parsed.path.startswith("/api/") and (not self._require_database() or not self._require_user()):
                return
            if parsed.path == "/api/migrate":
                admin = self._require_admin()
                if not admin:
                    return
                self._json(lambda: {"applied": repo.db.migrate()})
                return
            if parsed.path == "/api/change-password":
                def change_password() -> dict[str, Any]:
                    user = self._current_user()
                    if not user:
                        raise RuntimeError("unauthorized")
                    updated = repo.change_own_password(
                        int(user["id"]),
                        payload.get("current_password") or "",
                        payload.get("new_password") or "",
                    )
                    return {"user": public_user(updated)}
                self._json(change_password)
                return
            if parsed.path == "/api/contacts":
                def create_contact() -> dict[str, Any]:
                    user = self._current_user()
                    payload.setdefault("owner", user.get("display_name") or user.get("username"))
                    inserted, skipped = repo.upsert_contacts([payload], owner_user_id=int(user["id"]))
                    return {"inserted": inserted, "skipped": skipped}
                self._json(create_contact)
                return
            if parsed.path == "/api/import/csv":
                def import_csv() -> dict[str, Any]:
                    user = self._current_user()
                    contacts = parse_contacts_csv(payload.get("csv", ""), default_source=payload.get("source") or "csv_import")
                    for contact in contacts:
                        contact.setdefault("owner", user.get("display_name") or user.get("username"))
                    inserted, skipped = repo.upsert_contacts(contacts, owner_user_id=int(user["id"]))
                    return {"parsed": len(contacts), "inserted": inserted, "skipped": skipped}
                self._json(import_csv)
                return
            if parsed.path == "/api/import/company-seeds":
                def import_company_seeds() -> dict[str, Any]:
                    user = self._current_user()
                    seeds = parse_company_seed_csv(
                        payload.get("csv", ""),
                        default_location=payload.get("default_location") or "",
                        default_industry=payload.get("default_industry") or "",
                    )
                    requested = max(1, int(payload.get("per_company_limit") or 5)) * max(1, len(seeds))
                    snapshot = QuotaService(config, repo).snapshot(user)
                    remaining = min(snapshot["source"]["remaining_user"], snapshot["source"]["remaining_global"])
                    if requested > remaining:
                        raise RuntimeError(f"今日获客额度不足：需要 {requested}，剩余 {remaining}")
                    result = LinkedInPublicSearchService(config, repo).run_company_seeds(
                        seeds,
                        per_company_limit=max(1, int(payload.get("per_company_limit") or 5)),
                        user=user,
                        auto_queue=bool(payload.get("auto_queue", False)),
                    )
                    quota_result = QuotaService(config, repo).consume(user, "source", int(result.get("results") or 0))
                    queued = sent = 0
                    if payload.get("auto_queue") or payload.get("auto_send"):
                        queued = QueueService(repo).queue(int(result.get("promoted") or 0), user=user)
                    if payload.get("auto_send"):
                        send_snapshot = QuotaService(config, repo).snapshot(user)
                        send_limit = min(int(queued or result.get("promoted") or 0), send_snapshot["send"]["remaining_user"], send_snapshot["send"]["remaining_global"])
                        if send_limit > 0:
                            sent = OutreachService(config, repo).send_due(send_limit, user=user)
                            quota_result = QuotaService(config, repo).consume(user, "send", sent)
                    return {"parsed": len(seeds), "result": {**result, "queued": queued, "sent": sent}, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json(import_company_seeds)
                return
            if parsed.path == "/api/source":
                criteria = {
                    "company_website": normalize_company_website(payload.get("company_website", "")),
                    "role": payload.get("role", "") or payload.get("title", ""),
                    "title": payload.get("role", "") or payload.get("title", ""),
                    "industry": payload.get("industry", ""),
                    "location": payload.get("location", ""),
                    "company_size": payload.get("company_size", ""),
                }
                def source_with_quota() -> dict[str, Any]:
                    user = self._current_user()
                    requested = int(payload.get("limit", 25))
                    snapshot = QuotaService(config, repo).snapshot(user)
                    remaining = min(snapshot["source"]["remaining_user"], snapshot["source"]["remaining_global"])
                    limit = min(requested, max(0, remaining))
                    if limit <= 0:
                        raise RuntimeError("今日获客配额已用完")
                    result = SourcingService(config, repo).source(
                        criteria,
                        limit,
                        owner_user_id=int(user["id"]),
                        owner=user.get("display_name") or user.get("username"),
                    )
                    quota_result = QuotaService(config, repo).consume(user, "source", limit)
                    return {"result": result, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json(source_with_quota)
                return
            if parsed.path == "/api/source/linkedin-public-search":
                criteria = {
                    "role": payload.get("role", "") or payload.get("title", ""),
                    "title": payload.get("role", "") or payload.get("title", ""),
                    "industry": payload.get("industry", ""),
                    "location": payload.get("location", ""),
                    "company_keyword": payload.get("company_keyword", ""),
                    "auto_domain_lookup": bool(payload.get("auto_domain_lookup", True)),
                    "auto_generate_email_candidates": bool(payload.get("auto_generate_email_candidates", True)),
                    "high_confidence_verify": bool(payload.get("high_confidence_verify", True)),
                }
                def linkedin_source_with_quota() -> dict[str, Any]:
                    user = self._current_user()
                    requested = int(payload.get("limit", 25))
                    snapshot = QuotaService(config, repo).snapshot(user)
                    remaining = min(snapshot["source"]["remaining_user"], snapshot["source"]["remaining_global"])
                    limit = min(requested, max(0, remaining))
                    if limit <= 0:
                        raise RuntimeError("今日获客配额已用完")
                    result = LinkedInPublicSearchService(config, repo).run(criteria, limit, user=user)
                    quota_result = QuotaService(config, repo).consume(user, "source", int(result.get("results") or 0))
                    return {"result": result, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json(linkedin_source_with_quota)
                return
            if parsed.path == "/api/search-results/promote":
                self._json(lambda: LinkedInPublicSearchService(config, repo).promote_result(int(payload["result_id"]), user=self._current_user()))
                return
            if parsed.path == "/api/email-candidates/adopt":
                self._json(lambda: LinkedInPublicSearchService(config, repo).adopt_candidate(int(payload["contact_id"]), payload.get("email") or "", user=self._current_user()))
                return
            if parsed.path == "/api/enrich":
                self._json(lambda: _result("enriched", EnrichmentService(config, repo).enrich(int(payload.get("limit", 25)), user=self._current_user())))
                return
            if parsed.path == "/api/enrich-one":
                if not self._require_contact_access(int(payload["contact_id"])):
                    return
                self._json(lambda: EnrichmentService(config, repo).enrich_contact(int(payload["contact_id"])))
                return
            if parsed.path == "/api/social-enrich":
                self._json(lambda: _result("social_enriched", SocialEnrichmentService(config, repo).enrich(int(payload.get("limit", 25)), user=self._current_user())))
                return
            if parsed.path == "/api/social-enrich-one":
                if not self._require_contact_access(int(payload["contact_id"])):
                    return
                self._json(lambda: SocialEnrichmentService(config, repo).enrich_contact(int(payload["contact_id"])))
                return
            if parsed.path == "/api/queue":
                self._json(lambda: {"queued": QueueService(repo).queue(int(payload.get("limit", 25)), user=self._current_user())})
                return
            if parsed.path == "/api/queue-one":
                self._json(lambda: {"queued": QueueService(repo).queue_contact(int(payload["contact_id"]), user=self._current_user())})
                return
            if parsed.path == "/api/send":
                def send_with_quota() -> dict[str, Any]:
                    user = self._current_user()
                    requested = int(payload.get("limit", 25))
                    snapshot = QuotaService(config, repo).snapshot(user)
                    remaining = min(snapshot["send"]["remaining_user"], snapshot["send"]["remaining_global"])
                    limit = min(requested, max(0, remaining))
                    if limit <= 0:
                        raise RuntimeError("今日发信配额已用完")
                    sent = OutreachService(config, repo).send_due(limit, user=user)
                    quota_result = QuotaService(config, repo).consume(user, "send", sent)
                    return {"sent": sent, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json(send_with_quota)
                return
            if parsed.path == "/api/send-one":
                def send_one_with_quota() -> dict[str, Any]:
                    user = self._current_user()
                    snapshot = QuotaService(config, repo).snapshot(user)
                    if min(snapshot["send"]["remaining_user"], snapshot["send"]["remaining_global"]) <= 0:
                        raise RuntimeError("今日发信配额已用完")
                    sent = OutreachService(config, repo).send_contact(int(payload["contact_id"]), user=user)
                    quota_result = QuotaService(config, repo).consume(user, "send", 1 if sent else 0)
                    return {"sent": sent, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json(send_one_with_quota)
                return
            if parsed.path == "/api/scheduler":
                admin = self._require_admin()
                if not admin:
                    return
                def run_scheduler() -> dict[str, str]:
                    SchedulerService(config, repo).run_once(
                        int(payload.get("enrich_limit", 25)),
                        int(payload.get("queue_limit", 25)),
                        int(payload.get("send_limit", 25)),
                    )
                    return {"status": "ok"}
                self._json(run_scheduler)
                return
            if parsed.path == "/api/mark":
                def mark() -> dict[str, Any]:
                    if not self._has_contact_access(int(payload["contact_id"])):
                        raise RuntimeError("Contact not found")
                    repo.mark_status(int(payload["contact_id"]), payload["status"], notes=payload.get("notes"))
                    return {"ok": True}
                self._json(mark)
                return
            if parsed.path == "/api/lifecycle":
                def lifecycle() -> dict[str, Any]:
                    if not self._has_contact_access(int(payload["contact_id"])):
                        raise RuntimeError("Contact not found")
                    return LifecycleService(repo).update(
                        int(payload["contact_id"]),
                        lifecycle_stage=payload.get("lifecycle_stage"),
                        disposition=payload.get("disposition"),
                        next_action_at=payload.get("next_action_at"),
                        notes=payload.get("notes"),
                        lost_reason=payload.get("lost_reason"),
                        owner=payload.get("owner"),
                    )
                self._json(lifecycle)
                return
            if parsed.path == "/api/profile-agent":
                if not self._require_contact_access(int(payload["contact_id"])):
                    return
                self._json(lambda: {"insights": ProfileAgentService(config, repo).summarize(int(payload["contact_id"]))})
                return
            if parsed.path == "/api/lifecycle-activity":
                def activity() -> dict[str, Any]:
                    if not self._has_contact_access(int(payload["contact_id"])):
                        raise RuntimeError("Contact not found")
                    return repo.add_lifecycle_activity(
                        int(payload["contact_id"]),
                        lifecycle_stage=payload.get("lifecycle_stage") or "lead",
                        activity_type=payload.get("activity_type") or "note",
                        title=payload.get("title"),
                        content=payload.get("content") or "",
                        created_by=payload.get("created_by") or "dashboard",
                    )
                self._json(activity)
                return
            if parsed.path == "/api/stage-agent":
                def stage_agent() -> dict[str, Any]:
                    if not self._has_contact_access(int(payload["contact_id"])):
                        raise RuntimeError("Contact not found")
                    return {
                        "analysis": StageAgentService(config, repo).analyze(
                            int(payload["contact_id"]),
                            activity_id=payload.get("activity_id"),
                            content=payload.get("content"),
                            stage=payload.get("lifecycle_stage"),
                            activity_type=payload.get("activity_type"),
                        )
                    }
                self._json(stage_agent)
                return
            if parsed.path == "/api/email-draft":
                if not self._require_contact_access(int(payload["contact_id"])):
                    return
                self._json(lambda: PersonalizedEmailService(config, repo).draft(
                    int(payload["contact_id"]),
                    mode=payload.get("mode") or "ai",
                    custom_subject=payload.get("subject"),
                    custom_body=payload.get("body"),
                ))
                return
            if parsed.path == "/api/send-custom":
                def send_custom() -> dict[str, Any]:
                    user = self._current_user()
                    if not repo.get_contact_for_user(int(payload["contact_id"]), user):
                        raise RuntimeError("Contact not found")
                    snapshot = QuotaService(config, repo).snapshot(user)
                    if min(snapshot["send"]["remaining_user"], snapshot["send"]["remaining_global"]) <= 0:
                        raise RuntimeError("今日发信配额已用完")
                    result = PersonalizedEmailService(config, repo).send(
                        int(payload["contact_id"]),
                        subject=payload.get("subject") or "",
                        body=payload.get("body") or "",
                        mode=payload.get("mode") or "custom",
                        user=user,
                    )
                    quota_result = QuotaService(config, repo).consume(user, "send", 1 if result.get("sent") else 0)
                    return {**result, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json(send_custom)
                return
            if parsed.path == "/api/admin/users":
                admin = self._require_admin()
                if not admin:
                    return
                def add_user() -> dict[str, Any]:
                    return repo.create_user(
                        username=payload["username"],
                        password=payload["password"],
                        display_name=payload.get("display_name") or payload["username"],
                        role=payload.get("role") or "sales",
                        daily_source_limit=int(payload.get("daily_source_limit") or 100),
                        daily_send_limit=int(payload.get("daily_send_limit") or 100),
                        must_change_password=True,
                    )
                self._json(add_user)
                return
            if parsed.path == "/api/admin/user":
                admin = self._require_admin()
                if not admin:
                    return
                def update_user() -> dict[str, Any]:
                    user_id = int(payload["user_id"])
                    if payload.get("password"):
                        repo.reset_user_password(user_id, payload["password"])
                    return repo.update_user(
                        user_id,
                        display_name=payload.get("display_name"),
                        role=payload.get("role"),
                        daily_source_limit=int(payload["daily_source_limit"]) if payload.get("daily_source_limit") is not None else None,
                        daily_send_limit=int(payload["daily_send_limit"]) if payload.get("daily_send_limit") is not None else None,
                        active=payload.get("active"),
                    )
                self._json(update_user)
                return
            if parsed.path == "/api/admin/sender":
                admin = self._require_admin()
                if not admin:
                    return
                def update_sender() -> dict[str, Any]:
                    return repo.update_sender_account(
                        int(payload["sender_id"]),
                        name=payload.get("name"),
                        email=payload.get("email"),
                        provider=payload.get("provider"),
                        daily_limit=int(payload["daily_limit"]) if payload.get("daily_limit") is not None else None,
                        warmup_stage=payload.get("warmup_stage"),
                        active=payload.get("active"),
                    )
                self._json(update_sender)
                return
            if parsed.path == "/api/blacklist":
                def blacklist() -> dict[str, Any]:
                    repo.add_blacklist(email=payload.get("email"), domain=payload.get("domain"), reason=payload.get("reason"))
                    return {"ok": True}
                self._json(blacklist)
                return
            if parsed.path == "/api/webhook":
                def webhook() -> dict[str, Any]:
                    notifier = SlackClient(config.raw.get("notifications", {}).get("slack_webhook_url"))
                    event = WebhookService(repo, notifier).process_payload(payload.get("provider", "manual"), payload.get("payload", {}))
                    return {"event_type": event}
                self._json(webhook)
                return
            if parsed.path.startswith("/webhooks/"):
                provider = parsed.path.removeprefix("/webhooks/") or payload.get("provider", "resend")
                def public_webhook() -> dict[str, Any]:
                    verified_payload = payload
                    if provider == "resend":
                        verified_payload = self._verify_resend_webhook(payload)
                    event_type = verified_payload.get("type") or verified_payload.get("event_type") or "unknown"
                    external_id = self.headers.get("svix-id") or verified_payload.get("id")
                    if not repo.record_webhook_delivery(provider, event_type, verified_payload, external_id):
                        return {"event_type": event_type, "duplicate": True}
                    notifier = SlackClient(config.raw.get("notifications", {}).get("slack_webhook_url"))
                    event = WebhookService(repo, notifier).process_payload(provider, verified_payload)
                    return {"event_type": event}
                self._json(public_webhook)
                return
            self.send_error(404)

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError as exc:
                self._raw_body = b""
                raise ValueError("invalid_content_length") from exc
            max_bytes = int(config.raw.get("app", {}).get("max_json_body_bytes") or MAX_JSON_BODY_BYTES)
            if length > max_bytes:
                self._raw_body = b""
                raise ValueError("json_body_too_large")
            if length == 0:
                self._raw_body = b""
                return {}
            self._raw_body = self.rfile.read(length)
            try:
                return json.loads(self._raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid_json") from exc

        def _json(self, fn) -> None:
            try:
                self._send_json({"ok": True, "data": fn()})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)

        def _current_user(self) -> dict[str, Any] | None:
            token = parse_session_cookie(self.headers.get("Cookie"))
            try:
                return repo.get_session_user(token)
            except Exception:
                return None

        def _require_database(self) -> bool:
            if check_database(repo)["ok"]:
                return True
            self._send_json({"ok": False, "error": "database_unavailable"}, status=503)
            return False

        def _require_user(self) -> dict[str, Any] | None:
            user = self._current_user()
            if user:
                return user
            self._send_json({"ok": False, "error": "unauthorized"}, status=401)
            return None

        def _require_admin(self) -> dict[str, Any] | None:
            user = self._require_user()
            if not user:
                return None
            if user.get("role") == "admin":
                return user
            self._send_json({"ok": False, "error": "admin_required"}, status=403)
            return None

        def _has_contact_access(self, contact_id: int) -> bool:
            user = self._current_user()
            return bool(user and repo.get_contact_for_user(contact_id, user))

        def _require_contact_access(self, contact_id: int) -> bool:
            if self._has_contact_access(contact_id):
                return True
            self._send_json({"ok": False, "error": "contact_not_found"}, status=404)
            return False

        def _send_json(self, data: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> None:
            body = json.dumps(data, ensure_ascii=False, default=_json_default).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_security_headers()
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _verify_resend_webhook(self, parsed_payload: dict[str, Any]) -> dict[str, Any]:
            secret = config.raw.get("webhooks", {}).get("resend_secret") or config.raw.get("notifications", {}).get("resend_webhook_secret")
            if not secret:
                return parsed_payload
            try:
                from svix.webhooks import Webhook

                headers = {
                    "svix-id": self.headers.get("svix-id", ""),
                    "svix-timestamp": self.headers.get("svix-timestamp", ""),
                    "svix-signature": self.headers.get("svix-signature", ""),
                }
                return Webhook(secret).verify(self._raw_body.decode("utf-8"), headers)
            except Exception as exc:
                raise RuntimeError(f"Invalid Resend webhook signature: {exc}") from exc

        def _send_csv(self, text: str) -> None:
            body = text.encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=contacts.csv")
            self._send_security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists() or not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self._send_security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_pixel(self) -> None:
            body = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.send_header("Cache-Control", "no-store")
            self._send_security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; connect-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            if secure_cookie:
                self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return DashboardHandler


def _result(label: str, value: Any) -> dict[str, Any]:
    return {label: value}


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
