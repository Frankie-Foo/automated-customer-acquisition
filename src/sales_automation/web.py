from __future__ import annotations

import argparse
import hmac
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
from .importers import parse_company_seed_csv, parse_company_seed_upload, parse_contacts_csv
from .linkedin_public_search import LinkedInPublicSearchService
from .outbound_identity import parse_signed_reply_route
from .quotas import QuotaService
from .rendering import verify_tracking_token
from .sender_pool import SenderPoolManager
from .services import AccountResearchService, AutomationRunService, EnrichmentService, LeadWorkflowService, LifecycleService, OutreachService, PersonalizedEmailService, ProfileAgentService, QueueService, SchedulerService, SocialEnrichmentService, SourcingService, StageAgentService, WebhookService
from .vps_sso import VpsSsoError, VpsSsoService


def normalize_company_website(value: str | None) -> str:
    if not value:
        return ""
    from .clients import _domain_from_website

    return _domain_from_website(value) or ""


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


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
        automation = AutomationRunService(config, repo)
        for run in repo.recover_automation_runs():
            automation.start(int(run["id"]), user=repo.get_active_user(int(run["owner_user_id"])))
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
    sso_cookie_same_site = "None" if secure_cookie and _truthy(config.raw.get("sso", {}).get("iframe_cookie")) else "Lax"

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_file(static_path("index.html"), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/live":
                self._send_json({"ok": True, "data": {"status": "live"}})
                return
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
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
                self._send_json({"ok": True, "data": {"ok": True}}, headers={"Set-Cookie": clear_session_cookie(secure=secure_cookie, same_site=sso_cookie_same_site)})
                return
            if parsed.path.startswith("/api/") and not self._require_user():
                return
            if parsed.path == "/api/summary":
                self._json(lambda: repo.dashboard_summary(user=self._current_user()))
                return
            if parsed.path == "/api/owner-import-report":
                self._json(lambda: repo.owner_import_report(user=self._current_user()))
                return
            if parsed.path == "/api/readiness":
                admin = self._require_admin()
                if not admin:
                    return
                self._json(lambda: check_readiness(config, repo))
                return
            if parsed.path == "/unsubscribe":
                qs = parse_qs(parsed.query)
                source = "signed_unsubscribe_link"
                try:
                    token = qs.get("token", [""])[0]
                    if token:
                        verified = verify_tracking_token(token, "unsubscribe", _tracking_secret(config))
                        contact_id = int(verified["contact_id"])
                    else:
                        contact_id = int(qs.get("contact_id", ["0"])[0])
                        if not repo.allow_legacy_tracking(contact_id):
                            raise ValueError("invalid_tracking_token")
                        source = "legacy_unsubscribe_link"
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return
                repo.record_event(contact_id, "unsubscribed", {"source": source})
                self._send_html("<!doctype html><title>Unsubscribed</title><p>You have been unsubscribed.</p>")
                return
            if parsed.path == "/track/open":
                qs = parse_qs(parsed.query)
                source = "signed_tracking_pixel"
                try:
                    token = qs.get("token", [""])[0]
                    if token:
                        verified = verify_tracking_token(token, "open", _tracking_secret(config))
                        contact_id = int(verified["contact_id"])
                        step = int(verified.get("step") or 0)
                    else:
                        contact_id = int(qs.get("contact_id", ["0"])[0])
                        step = int(qs.get("step", ["0"])[0])
                        if not repo.allow_legacy_tracking(contact_id, step):
                            raise ValueError("invalid_tracking_token")
                        source = "legacy_tracking_pixel"
                except ValueError:
                    self._send_pixel()
                    return
                repo.record_event(contact_id, "opened", {"source": source, "step": step})
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
            if parsed.path == "/api/sent-emails":
                qs = parse_qs(parsed.query)
                search = qs.get("search", [""])[0] or None
                limit = int(qs.get("limit", ["100"])[0])
                self._json(lambda: {"emails": repo.list_sent_emails(user=self._current_user(), limit=limit, search=search)})
                return
            if parsed.path == "/api/ops-report":
                self._json(lambda: repo.operations_report(user=self._current_user()))
                return
            if parsed.path == "/api/search-tasks":
                self._json(lambda: {"tasks": repo.list_lead_search_tasks(user=self._current_user())})
                return
            if parsed.path == "/api/automation-runs":
                self._json(lambda: {"runs": repo.list_automation_runs(user=self._current_user())})
                return
            if parsed.path == "/api/followup-tasks":
                qs = parse_qs(parsed.query)
                status = qs.get("status", ["open"])[0] or "open"
                limit = int(qs.get("limit", ["100"])[0])
                self._json(lambda: {"tasks": repo.list_followup_tasks(user=self._current_user(), status=status, limit=limit)})
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
            if parsed.path == "/api/admin/region-rules":
                admin = self._require_admin()
                if not admin:
                    return
                self._json(lambda: {"rules": repo.region_assignment_rules()})
                return
            if parsed.path == "/api/admin/audit-logs":
                admin = self._require_admin()
                if not admin:
                    return
                qs = parse_qs(parsed.query)
                limit = int(qs.get("limit", ["100"])[0])
                self._json(lambda: {"logs": repo.list_audit_logs(limit=limit)})
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
            if parsed.path == "/api/logout":
                token = parse_session_cookie(self.headers.get("Cookie"))
                repo.delete_session(token)
                self._send_json(
                    {"ok": True, "data": {"ok": True}},
                    headers={"Set-Cookie": clear_session_cookie(secure=secure_cookie, same_site=sso_cookie_same_site)},
                )
                return
            if parsed.path == "/api/login":
                if not self._require_database():
                    return
                user = repo.authenticate_user(payload.get("username", ""), payload.get("password", ""))
                if not user:
                    self._send_json({"ok": False, "error": "用户名或密码错误"}, status=401)
                    return
                token = repo.create_session(int(user["id"]))
                self._audit("login", user=user, summary="账号密码登录成功", metadata={"method": "password"})
                quota_snapshot = QuotaService(config, repo).snapshot(user)
                self._send_json(
                    {"ok": True, "data": {"user": public_user(user), "usage": quota_snapshot["user_usage"], "quotas": quota_snapshot}},
                    headers={"Set-Cookie": session_cookie(token, secure=secure_cookie, same_site=sso_cookie_same_site)},
                )
                return
            if parsed.path == "/api/auth/vps-login":
                if not self._require_database():
                    return
                try:
                    user_id = int(payload.get("userId") or payload.get("user_id") or 0)
                except Exception:
                    self._send_json({"ok": False, "error": "缺少 sessionID 或 userId"}, status=400)
                    return
                session_id = str(payload.get("sessionID") or payload.get("session_id") or "").strip()
                try:
                    profile = VpsSsoService(config).verify(session_id=session_id, user_id=user_id)
                    sso = config.raw.get("sso", {})
                    user = repo.vps_login_user(
                        odoo_user_id=profile.odoo_user_id,
                        username=profile.login,
                        display_name=profile.name,
                        email=profile.email,
                        vps_barcode=profile.barcode,
                        department=profile.department,
                        role=str(sso.get("default_role") or "sales"),
                        daily_source_limit=int(config.raw.get("quotas", {}).get("default_user_daily_source") or 100),
                        daily_send_limit=int(config.raw.get("quotas", {}).get("default_user_daily_send") or 200),
                        auto_create=_truthy(sso.get("auto_create_users", True)),
                    )
                except VpsSsoError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=exc.status)
                    return
                except RuntimeError as exc:
                    status = 403 if str(exc) in {"vps_user_disabled", "vps_user_not_mapped"} else 500
                    message = "账号已被禁用" if str(exc) == "vps_user_disabled" else "未映射本地账号，请联系管理员"
                    self._send_json({"ok": False, "error": message}, status=status)
                    return
                token = repo.create_session(int(user["id"]))
                self._audit("login", user=user, summary="Odoo/VPS 单点登录成功", metadata={"method": "vps_sso", "odoo_user_id": profile.odoo_user_id, "barcode": profile.barcode})
                quota_snapshot = QuotaService(config, repo).snapshot(user)
                self._send_json(
                    {"ok": True, "data": {"next": "/", "user": public_user(user), "usage": quota_snapshot["user_usage"], "quotas": quota_snapshot}},
                    headers={"Set-Cookie": session_cookie(token, secure=secure_cookie, same_site=sso_cookie_same_site)},
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
                self._json_audit("migrate", lambda: {"applied": repo.db.migrate()}, summary="管理员执行数据库迁移")
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
                self._json_audit("change_password", change_password, summary="用户修改登录密码")
                return
            if parsed.path == "/api/contacts":
                def create_contact() -> dict[str, Any]:
                    user = self._current_user()
                    result = LeadWorkflowService(repo).ingest_contacts(
                        [payload],
                        user=user,
                        source_type="manual_entry",
                        source_ref="web_form",
                    )
                    return {**result, "skipped": result["duplicates"]}
                self._json_audit(
                    "create_contact",
                    create_contact,
                    summary="手动新增客户",
                    metadata=lambda data: {"inserted": data.get("inserted"), "skipped": data.get("skipped")},
                )
                return
            if parsed.path == "/api/import/csv":
                def import_csv() -> dict[str, Any]:
                    user = self._current_user()
                    source_ref = str(payload.get("source") or "csv_import")[:500]
                    contacts = parse_contacts_csv(payload.get("csv", ""), default_source=source_ref)
                    result = LeadWorkflowService(repo).ingest_contacts(
                        contacts,
                        user=user,
                        source_type="csv_import",
                        source_ref=source_ref,
                    )
                    return {**result, "skipped": result["duplicates"]}
                self._json_audit(
                    "import_csv",
                    import_csv,
                    summary="导入 CSV 客户",
                    metadata=lambda data: {"parsed": data.get("parsed"), "inserted": data.get("inserted"), "skipped": data.get("skipped")},
                )
                return
            if parsed.path == "/api/import/company-seeds":
                if payload.get("auto_send") and not self._require_admin():
                    return
                def import_company_seeds() -> dict[str, Any]:
                    user = self._current_user()
                    if payload.get("file_base64"):
                        seeds = parse_company_seed_upload(
                            filename=payload.get("filename") or "upload.xlsx",
                            content_base64=payload.get("file_base64") or "",
                            default_location=payload.get("default_location") or "",
                            default_industry=payload.get("default_industry") or "",
                        )
                    else:
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
                    batch_report = repo.company_seed_batch_report(
                        [int(item["task_id"]) for item in result.get("tasks", []) if item.get("task_id")],
                        user=user,
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
                    return {"parsed": len(seeds), "result": {**result, "queued": queued, "sent": sent}, "batch_report": batch_report, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json_audit(
                    "import_company_seeds",
                    import_company_seeds,
                    summary="导入公司种子并获客",
                    metadata=lambda data: {
                        "parsed": data.get("parsed"),
                        "results": data.get("result", {}).get("results"),
                        "promoted": data.get("result", {}).get("promoted"),
                        "queued": data.get("result", {}).get("queued"),
                        "sent": data.get("result", {}).get("sent"),
                    },
                )
                return
            if parsed.path == "/api/automation-runs/company-seeds":
                def create_company_seed_run() -> dict[str, Any]:
                    user = self._current_user()
                    if payload.get("file_base64"):
                        seeds = parse_company_seed_upload(
                            filename=payload.get("filename") or "upload.xlsx",
                            content_base64=payload.get("file_base64") or "",
                            default_location=payload.get("default_location") or "",
                            default_industry=payload.get("default_industry") or "",
                        )
                    else:
                        seeds = parse_company_seed_csv(
                            payload.get("csv", ""),
                            default_location=payload.get("default_location") or "",
                            default_industry=payload.get("default_industry") or "",
                        )
                    key = str(payload.get("idempotency_key") or f"upload-{user['id']}-{datetime.now().timestamp()}")[:200]
                    run = AutomationRunService(config, repo).create_company_seed_run(
                        seeds,
                        per_company_limit=max(1, int(payload.get("per_company_limit") or 5)),
                        user=user,
                        idempotency_key=key,
                        auto_prepare_drafts=bool(payload.get("auto_prepare_drafts", True)),
                    )
                    return {"run": run, "parsed": len(seeds)}
                self._json_audit(
                    "create_company_seed_automation",
                    create_company_seed_run,
                    target_type="automation_run",
                    target_id=lambda data: data.get("run", {}).get("id"),
                    summary="创建批量获客任务",
                    metadata=lambda data: {"parsed": data.get("parsed"), "run_id": data.get("run", {}).get("id")},
                )
                return
            if parsed.path == "/api/automation-runs/action":
                def automation_action() -> dict[str, Any]:
                    user = self._current_user()
                    run_id = int(payload.get("run_id") or 0)
                    action = str(payload.get("action") or "")
                    service = AutomationRunService(config, repo)
                    if action == "pause":
                        return {"run": service.pause(run_id, user=user)}
                    if action in {"resume", "retry"}:
                        return {"run": service.resume(run_id, user=user)}
                    raise ValueError("Unsupported automation action")
                self._json_audit(
                    "automation_run_action",
                    automation_action,
                    target_type="automation_run",
                    target_id=payload.get("run_id"),
                    summary="暂停或恢复批量获客任务",
                    metadata={"action": payload.get("action")},
                )
                return
            if parsed.path == "/api/followup-tasks/complete":
                def complete_followup_task() -> dict[str, Any]:
                    task = repo.complete_followup_task(
                        int(payload.get("task_id") or 0),
                        user=self._current_user(),
                        outcome=str(payload.get("outcome") or "completed")[:500],
                    )
                    if not task:
                        raise RuntimeError("待办不存在或无权操作")
                    return {"task": task}
                self._json_audit(
                    "complete_followup_task",
                    complete_followup_task,
                    target_type="followup_task",
                    target_id=payload.get("task_id"),
                    summary="完成销售跟进待办",
                )
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
                    )
                    quota_result = QuotaService(config, repo).consume(user, "source", limit)
                    return {"result": result, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json_audit(
                    "source",
                    source_with_quota,
                    summary="自动获客",
                    metadata=lambda data: {"requested": payload.get("limit"), "result": data.get("result")},
                )
                return
            if parsed.path == "/api/source/linkedin-public-search":
                criteria = {
                    "full_name": payload.get("full_name", "") or payload.get("person_name", ""),
                    "company_website": normalize_company_website(payload.get("company_website", "")),
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
                self._json_audit(
                    "linkedin_public_search",
                    linkedin_source_with_quota,
                    summary="LinkedIn 公网搜索获客",
                    metadata=lambda data: {"requested": payload.get("limit"), "result": data.get("result")},
                )
                return
            if parsed.path == "/api/search-results/promote":
                self._json_audit(
                    "promote_search_result",
                    lambda: LinkedInPublicSearchService(config, repo).promote_result(int(payload["result_id"]), user=self._current_user()),
                    target_type="lead_search_result",
                    target_id=payload.get("result_id"),
                    summary="搜索候选入库",
                )
                return
            if parsed.path == "/api/email-candidates/adopt":
                self._json_audit(
                    "adopt_email_candidate",
                    lambda: LinkedInPublicSearchService(config, repo).adopt_candidate(int(payload["contact_id"]), payload.get("email") or "", user=self._current_user()),
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="采用邮箱候选",
                    metadata={"email": payload.get("email") or ""},
                )
                return
            if parsed.path == "/api/customer-pool/claim":
                self._json_audit(
                    "claim_public_contact",
                    lambda: {"contact": repo.claim_public_contact(int(payload["contact_id"]), self._current_user())},
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="领取公共池客户",
                )
                return
            if parsed.path == "/api/customer-pool/return":
                self._json_audit(
                    "return_contact_to_public",
                    lambda: {"contact": repo.return_contact_to_public(int(payload["contact_id"]), self._current_user(), reason=payload.get("reason") or "manual_return")},
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="客户退回公共池",
                    metadata={"reason": payload.get("reason") or "manual_return"},
                )
                return
            if parsed.path == "/api/customer-pool/auto-assign":
                admin = self._require_admin()
                if not admin:
                    return
                self._json_audit(
                    "auto_assign_public_pool",
                    lambda: repo.auto_assign_public_pool(limit=int(payload.get("limit") or 100)),
                    summary="管理员按地区分配公共池",
                    metadata={"limit": int(payload.get("limit") or 100)},
                )
                return
            if parsed.path == "/api/customer-pool/recycle-stale":
                admin = self._require_admin()
                if not admin:
                    return
                self._json_audit(
                    "recycle_stale_private_pool",
                    lambda: {"recycled": repo.recycle_stale_private_pool(limit=int(payload.get("limit") or 100))},
                    summary="管理员回收停滞客户",
                    metadata={"limit": int(payload.get("limit") or 100)},
                )
                return
            if parsed.path == "/api/enrich":
                self._json_audit(
                    "enrich",
                    lambda: _result("enriched", EnrichmentService(config, repo).enrich(int(payload.get("limit", 25)), user=self._current_user())),
                    summary="批量富化邮箱",
                    metadata=lambda data: {"enriched": data.get("enriched"), "limit": int(payload.get("limit", 25))},
                )
                return
            if parsed.path == "/api/enrich-one":
                if not self._require_private_contact_access(int(payload["contact_id"])):
                    return
                self._json_audit(
                    "enrich_one",
                    lambda: EnrichmentService(config, repo).enrich_contact(int(payload["contact_id"])),
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="单客户富化邮箱",
                )
                return
            if parsed.path == "/api/social-enrich":
                self._json_audit(
                    "social_enrich",
                    lambda: _result("social_enriched", SocialEnrichmentService(config, repo).enrich(int(payload.get("limit", 25)), user=self._current_user())),
                    summary="批量富化社媒",
                    metadata=lambda data: {"social_enriched": data.get("social_enriched"), "limit": int(payload.get("limit", 25))},
                )
                return
            if parsed.path == "/api/social-enrich-one":
                if not self._require_private_contact_access(int(payload["contact_id"])):
                    return
                self._json_audit(
                    "social_enrich_one",
                    lambda: SocialEnrichmentService(config, repo).enrich_contact(int(payload["contact_id"])),
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="单客户富化社媒",
                )
                return
            if parsed.path == "/api/queue":
                self._json_audit(
                    "queue",
                    lambda: {"queued": QueueService(repo).queue(int(payload.get("limit", 25)), user=self._current_user())},
                    summary="批量加入发送队列",
                    metadata=lambda data: {"queued": data.get("queued"), "limit": int(payload.get("limit", 25))},
                )
                return
            if parsed.path == "/api/queue-one":
                self._json_audit(
                    "queue_one",
                    lambda: {"queued": QueueService(repo).queue_contact(int(payload["contact_id"]), user=self._current_user())},
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="单客户加入发送队列",
                )
                return
            if parsed.path == "/api/send":
                admin = self._require_admin()
                if not admin:
                    return
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
                self._json_audit(
                    "send",
                    send_with_quota,
                    summary="批量发送邮件",
                    metadata=lambda data: {"sent": data.get("sent"), "requested": payload.get("limit")},
                )
                return
            if parsed.path == "/api/send-one":
                admin = self._require_admin()
                if not admin:
                    return
                def send_one_with_quota() -> dict[str, Any]:
                    user = self._current_user()
                    snapshot = QuotaService(config, repo).snapshot(user)
                    if min(snapshot["send"]["remaining_user"], snapshot["send"]["remaining_global"]) <= 0:
                        raise RuntimeError("今日发信配额已用完")
                    sent = OutreachService(config, repo).send_contact(int(payload["contact_id"]), user=user)
                    quota_result = QuotaService(config, repo).consume(user, "send", 1 if sent else 0)
                    return {"sent": sent, "usage": quota_result["user_usage"], "quotas": quota_result}
                self._json_audit(
                    "send_one",
                    send_one_with_quota,
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="单客户发送邮件",
                    metadata=lambda data: {"sent": data.get("sent")},
                )
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
                self._json_audit("scheduler", run_scheduler, summary="管理员手动运行调度")
                return
            if parsed.path == "/api/mark":
                def mark() -> dict[str, Any]:
                    if not self._has_private_contact_access(int(payload["contact_id"])):
                        raise RuntimeError("Contact not found")
                    repo.mark_status(int(payload["contact_id"]), payload["status"], notes=payload.get("notes"))
                    return {"ok": True}
                self._json_audit(
                    "mark_contact",
                    mark,
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="修改客户状态",
                    metadata={"status": payload.get("status")},
                )
                return
            if parsed.path == "/api/lifecycle":
                def lifecycle() -> dict[str, Any]:
                    if not self._has_private_contact_access(int(payload["contact_id"])):
                        raise RuntimeError("Contact not found")
                    return LifecycleService(repo).update(
                        int(payload["contact_id"]),
                        lifecycle_stage=payload.get("lifecycle_stage"),
                        disposition=payload.get("disposition"),
                        next_action_at=payload.get("next_action_at"),
                        notes=payload.get("notes"),
                        lost_reason=payload.get("lost_reason"),
                        owner=payload.get("owner"),
                        sabcd_stage=payload.get("sabcd_stage"),
                    )
                self._json_audit(
                    "update_lifecycle",
                    lifecycle,
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="推进客户生命周期",
                    metadata={
                        "lifecycle_stage": payload.get("lifecycle_stage"),
                        "sabcd_stage": payload.get("sabcd_stage"),
                        "disposition": payload.get("disposition"),
                    },
                )
                return
            if parsed.path == "/api/profile-agent":
                if not self._require_private_contact_access(int(payload["contact_id"])):
                    return
                self._json_audit(
                    "profile_agent",
                    lambda: {"insights": ProfileAgentService(config, repo).summarize(int(payload["contact_id"]))},
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="生成客户画像",
                )
                return
            if parsed.path == "/api/contact-research":
                user = self._current_user()
                if not self._require_private_contact_access(int(payload["contact_id"])):
                    return
                self._json_audit(
                    "contact_research",
                    lambda: {"research": AccountResearchService(config, repo).research(
                        int(payload["contact_id"]),
                        user=user,
                        force=_truthy(payload.get("force")),
                    )},
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="调研客户与实时新闻",
                )
                return
            if parsed.path == "/api/lifecycle-activity":
                def activity() -> dict[str, Any]:
                    if not self._has_private_contact_access(int(payload["contact_id"])):
                        raise RuntimeError("Contact not found")
                    contact_id = int(payload["contact_id"])
                    user = self._current_user()
                    activity_type = payload.get("activity_type") or "note"
                    saved = repo.add_lifecycle_activity(
                        contact_id,
                        lifecycle_stage=payload.get("lifecycle_stage") or "lead",
                        activity_type=activity_type,
                        title=payload.get("title"),
                        content=payload.get("content") or "",
                        created_by=payload.get("created_by") or "dashboard",
                    )
                    repo.record_interaction(
                        contact_id=contact_id,
                        user_id=int(user["id"]),
                        interaction_type=activity_type,
                        direction="inbound" if activity_type == "reply" else "outbound",
                        channel=_activity_channel(activity_type),
                        subject=payload.get("title"),
                        content=payload.get("content") or "",
                        outcome=payload.get("lifecycle_stage") or "lead",
                        source_ref=f"lifecycle_activity:{saved['id']}",
                    )
                    repo.close_open_followup_tasks(contact_id)
                    LeadWorkflowService(repo).ensure_next_task(contact_id, owner_user_id=int(user["id"]))
                    repo.refresh_contact_campaign_metrics(contact_id)
                    return saved
                self._json_audit(
                    "add_lifecycle_activity",
                    activity,
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="新增跟进记录",
                    metadata={"activity_type": payload.get("activity_type"), "lifecycle_stage": payload.get("lifecycle_stage")},
                )
                return
            if parsed.path == "/api/stage-agent":
                def stage_agent() -> dict[str, Any]:
                    if not self._has_private_contact_access(int(payload["contact_id"])):
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
                self._json_audit(
                    "stage_agent",
                    stage_agent,
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="AI 分析阶段记录",
                )
                return
            if parsed.path == "/api/email-draft":
                if not self._require_private_contact_access(int(payload["contact_id"])):
                    return
                self._json_audit("email_draft", lambda: PersonalizedEmailService(config, repo).draft(
                    int(payload["contact_id"]),
                    mode=payload.get("mode") or "ai",
                    custom_subject=payload.get("subject"),
                    custom_body=payload.get("body"),
                    user=self._current_user(),
                ), target_type="contact", target_id=payload.get("contact_id"), summary="生成邮件草稿", metadata={"mode": payload.get("mode") or "ai"})
                return
            if parsed.path == "/api/email-draft/approve":
                def approve_email_draft() -> dict[str, Any]:
                    user = self._current_user()
                    contact_id = int(payload["contact_id"])
                    if not repo.get_private_contact_for_user(contact_id, user):
                        raise RuntimeError("Contact not found")
                    draft = repo.approve_latest_email_draft(contact_id, user_id=int(user["id"]))
                    if not draft:
                        raise RuntimeError("No draft is available for approval")
                    repo.close_open_followup_tasks(contact_id)
                    contact = repo.get_contact(contact_id) or {}
                    repo.ensure_followup_task(
                        contact_id=contact_id,
                        assigned_user_id=int(user["id"]),
                        created_by_user_id=int(user["id"]),
                        task_type="send",
                        priority="high",
                        title=f"发送已审核邮件：{_contact_display_name(contact)}",
                        description="邮件已审核，可在配额和工作时间内发送。",
                        due_at=None,
                        trigger_rule="approved_draft_ready",
                        metadata={"draft_id": draft["id"], "generated_by": "draft_approval"},
                    )
                    return {"draft": draft}
                self._json_audit(
                    "approve_email_draft",
                    approve_email_draft,
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="审核并锁定邮件草稿",
                )
                return
            if parsed.path == "/api/send-custom":
                def send_custom() -> dict[str, Any]:
                    user = self._current_user()
                    if not repo.get_private_contact_for_user(int(payload["contact_id"]), user):
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
                self._json_audit(
                    "send_custom",
                    send_custom,
                    target_type="contact",
                    target_id=payload.get("contact_id"),
                    summary="发送自定义邮件",
                    metadata=lambda data: {"sent": data.get("sent"), "mode": payload.get("mode") or "custom"},
                )
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
                        daily_send_limit=int(payload.get("daily_send_limit") or 200),
                        reply_to_email=payload.get("reply_to_email"),
                        sender_alias_localpart=payload.get("sender_alias_localpart"),
                        must_change_password=True,
                    )
                self._json_audit(
                    "admin_create_user",
                    add_user,
                    target_type="sales_user",
                    summary="管理员创建销售账号",
                    metadata=lambda data: {"user_id": data.get("id"), "username": data.get("username"), "role": data.get("role")},
                )
                return
            if parsed.path == "/api/admin/region-rules":
                admin = self._require_admin()
                if not admin:
                    return
                def save_region_rules() -> dict[str, Any]:
                    raw_rules = payload.get("rules") or []
                    if not isinstance(raw_rules, list):
                        raise ValueError("rules must be a list")
                    rules: list[dict[str, Any]] = []
                    for item in raw_rules:
                        owner = str((item or {}).get("owner") or "").strip()
                        matches = [str(value).strip().lower() for value in ((item or {}).get("match") or []) if str(value).strip()]
                        if owner and matches:
                            rules.append({"owner": owner, "match": list(dict.fromkeys(matches))})
                    saved = repo.set_app_setting("customer_pool.region_assignments", rules, user_id=int(admin["id"]))
                    return {"rules": saved}
                self._json_audit(
                    "update_region_assignment_rules",
                    save_region_rules,
                    summary="更新客户地区分配规则",
                    metadata=lambda data: {"rule_count": len(data.get("rules") or [])},
                )
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
                        reply_to_email=payload.get("reply_to_email"),
                        sender_alias_localpart=payload.get("sender_alias_localpart"),
                        active=payload.get("active"),
                    )
                self._json_audit(
                    "admin_update_user",
                    update_user,
                    target_type="sales_user",
                    target_id=payload.get("user_id"),
                    summary="管理员更新销售账号",
                    metadata={
                        "fields": [key for key in ("password", "display_name", "role", "daily_source_limit", "daily_send_limit", "reply_to_email", "sender_alias_localpart", "active") if payload.get(key) is not None],
                    },
                )
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
                self._json_audit(
                    "admin_update_sender",
                    update_sender,
                    target_type="sender_account",
                    target_id=payload.get("sender_id"),
                    summary="管理员更新发件账号",
                    metadata={
                        "fields": [key for key in ("name", "email", "provider", "daily_limit", "warmup_stage", "active") if payload.get(key) is not None],
                    },
                )
                return
            if parsed.path == "/api/blacklist":
                admin = self._require_admin()
                if not admin:
                    return
                def blacklist() -> dict[str, Any]:
                    repo.add_blacklist(email=payload.get("email"), domain=payload.get("domain"), reason=payload.get("reason"))
                    return {"ok": True}
                self._json_audit("blacklist", blacklist, summary="加入黑名单", metadata={"email": payload.get("email"), "domain": payload.get("domain")})
                return
            if parsed.path == "/api/webhook":
                admin = self._require_admin()
                if not admin:
                    return
                def webhook() -> dict[str, Any]:
                    notifier = SlackClient(config.raw.get("notifications", {}).get("slack_webhook_url"))
                    event = WebhookService(repo, notifier, config=config).process_payload(payload.get("provider", "manual"), payload.get("payload", {}))
                    return {"event_type": event}
                self._json(webhook)
                return
            if parsed.path.startswith("/webhooks/"):
                if not self._require_database():
                    return
                provider = parsed.path.removeprefix("/webhooks/") or payload.get("provider", "resend")
                if provider == "inbound-email":
                    try:
                        self._verify_inbound_email_secret()
                    except RuntimeError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=401)
                        return
                    def inbound_email() -> dict[str, Any]:
                        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                        route = parse_signed_reply_route(config, [payload.get("to"), data.get("to"), data.get("recipient")])
                        contact_id = int(route["contact_id"]) if route else None
                        in_reply_to = data.get("in_reply_to") or payload.get("in_reply_to")
                        if not contact_id and in_reply_to:
                            contact_id = repo.find_contact_id_by_message_id(str(in_reply_to))
                        if not contact_id:
                            contact_id = repo.find_contact_id_by_email(str(data.get("from") or data.get("sender") or payload.get("from") or payload.get("sender") or ""))
                        if not contact_id:
                            raise RuntimeError("inbound_sender_not_matched")
                        external_id = data.get("message_id") or payload.get("message_id")
                        if external_id and hasattr(repo, "record_webhook_delivery"):
                            if not repo.record_webhook_delivery("inbound_email", "replied", payload, str(external_id)):
                                return {"event_type": "replied", "contact_id": contact_id, "duplicate": True}
                        route_state = {}
                        if hasattr(repo, "route_inbound_reply"):
                            route_state = repo.route_inbound_reply(contact_id, route.get("user_id") if route else None)
                        event_payload = {
                            "source": "inbound_email_webhook",
                            "from": data.get("from") or data.get("sender") or payload.get("from") or payload.get("sender"),
                            "to": data.get("to") or payload.get("to"),
                            "subject": data.get("subject") or payload.get("subject"),
                            "text": str(data.get("text") or data.get("body") or payload.get("text") or payload.get("body") or "")[:10000],
                            "message_id": external_id,
                            "in_reply_to": in_reply_to,
                            "reply_route": route,
                            "reply_owner_user_id": route_state.get("owner_user_id"),
                            "reply_assignment_pending": route_state.get("reply_assignment_pending", False),
                        }
                        repo.record_event(contact_id, "replied", event_payload)
                        repo.add_lifecycle_activity(
                            contact_id,
                            lifecycle_stage="replied",
                            activity_type="reply",
                            title=str(payload.get("subject") or "Email reply")[:300],
                            content=event_payload["text"] or "Reply received",
                            created_by="inbound_email",
                        )
                        if external_id and hasattr(repo, "mark_webhook_delivery_processed"):
                            repo.mark_webhook_delivery_processed("inbound_email", str(external_id))
                        return {"event_type": "replied", "contact_id": contact_id}
                    self._json(inbound_email)
                    return
                def public_webhook() -> dict[str, Any]:
                    verified_payload = payload
                    if provider == "resend":
                        verified_payload = self._verify_resend_webhook(payload)
                    event_type = verified_payload.get("type") or verified_payload.get("event_type") or "unknown"
                    external_id = self.headers.get("svix-id") or verified_payload.get("id")
                    if not repo.record_webhook_delivery(provider, event_type, verified_payload, external_id):
                        return {"event_type": event_type, "duplicate": True}
                    notifier = SlackClient(config.raw.get("notifications", {}).get("slack_webhook_url"))
                    event = WebhookService(repo, notifier, config=config).process_payload(provider, verified_payload)
                    repo.mark_webhook_delivery_processed(provider, external_id)
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

        def _json_audit(
            self,
            action: str,
            fn,
            *,
            summary: str | None = None,
            target_type: str | None = None,
            target_id=None,
            metadata=None,
        ) -> None:
            try:
                data = fn()
                audit_metadata = metadata(data) if callable(metadata) else (metadata or {})
                audit_target_id = target_id(data) if callable(target_id) else target_id
                self._audit(action, target_type=target_type, target_id=audit_target_id, summary=summary, metadata=audit_metadata)
                self._send_json({"ok": True, "data": data})
            except Exception as exc:
                self._audit(action, target_type=target_type, target_id=None if callable(target_id) else target_id, summary=summary, success=False, error=str(exc))
                self._send_json({"ok": False, "error": str(exc)}, status=500)

        def _audit(
            self,
            action: str,
            *,
            user: dict[str, Any] | None = None,
            target_type: str | None = None,
            target_id: str | int | None = None,
            summary: str | None = None,
            metadata: dict[str, Any] | None = None,
            success: bool = True,
            error: str | None = None,
        ) -> None:
            try:
                repo.record_audit_log(
                    user=user or self._current_user(),
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    summary=summary,
                    metadata=metadata or {},
                    ip_address=self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip() or self.client_address[0],
                    user_agent=self.headers.get("User-Agent", ""),
                    success=success,
                    error=error,
                )
            except Exception:
                pass

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

        def _has_private_contact_access(self, contact_id: int) -> bool:
            user = self._current_user()
            return bool(user and repo.get_private_contact_for_user(contact_id, user))

        def _require_contact_access(self, contact_id: int) -> bool:
            if self._has_contact_access(contact_id):
                return True
            self._send_json({"ok": False, "error": "contact_not_found"}, status=404)
            return False

        def _require_private_contact_access(self, contact_id: int) -> bool:
            if self._has_private_contact_access(contact_id):
                return True
            self._send_json({"ok": False, "error": "claim_required"}, status=403)
            return False

        def _send_json(self, data: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> None:
            try:
                body = json.dumps(data, ensure_ascii=False, default=_json_default).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                self.close_connection = True

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

        def _verify_inbound_email_secret(self) -> None:
            expected = str(config.raw.get("webhooks", {}).get("inbound_email_secret") or "").strip()
            supplied = str(self.headers.get("X-Inbound-Secret") or "").strip()
            if len(expected) < 24 or not hmac.compare_digest(expected, supplied):
                raise RuntimeError("invalid_inbound_email_secret")

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
            if path.parent.name == "assets":
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            else:
                self.send_header("Cache-Control", "no-cache")
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


def _activity_channel(activity_type: str) -> str:
    value = str(activity_type or "").lower()
    if "email" in value or value == "reply":
        return "email"
    if "meeting" in value or "appointment" in value:
        return "meeting"
    if "phone" in value or "call" in value:
        return "phone"
    if "whatsapp" in value:
        return "whatsapp"
    return "manual"


def _contact_display_name(contact: dict[str, Any]) -> str:
    person = " ".join(str(contact.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    return person or str(contact.get("company_name") or "客户")


def _tracking_secret(config: Any) -> str:
    return str(
        config.raw.get("app", {}).get("tracking_signing_secret")
        or config.raw.get("webhooks", {}).get("resend_secret")
        or ""
    ).strip()


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
