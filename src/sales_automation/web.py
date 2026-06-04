from __future__ import annotations

import argparse
import json
import mimetypes
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .clients import SlackClient
from .config import load_config
from .db import Database, Repository
from .importers import parse_contacts_csv
from .production import readiness
from .services import EnrichmentService, LifecycleService, OutreachService, PersonalizedEmailService, ProfileAgentService, QueueService, SchedulerService, SocialEnrichmentService, SourcingService, StageAgentService, WebhookService


def normalize_company_website(value: str | None) -> str:
    if not value:
        return ""
    from .clients import _domain_from_website

    return _domain_from_website(value) or ""

STATIC_DIR = Path(__file__).parent / "web_static"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="salesbot-web")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    repo = Repository(Database(config))
    handler = make_handler(config, repo)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


def make_handler(config, repo: Repository):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if parsed.path.startswith("/static/"):
                self._send_file(STATIC_DIR / parsed.path.removeprefix("/static/"))
                return
            if parsed.path == "/api/summary":
                self._json(lambda: repo.dashboard_summary())
                return
            if parsed.path == "/api/readiness":
                self._json(lambda: readiness(config))
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
                limit = int(qs.get("limit", ["100"])[0])
                self._json(lambda: {"contacts": repo.list_contacts(status=status, search=search, limit=limit)})
                return
            if parsed.path == "/api/lifecycle":
                self._json(lambda: repo.lifecycle_summary())
                return
            if parsed.path == "/api/contact-detail":
                qs = parse_qs(parsed.query)
                contact_id = int(qs.get("contact_id", ["0"])[0])
                self._json(lambda: repo.contact_detail(contact_id) or {})
                return
            if parsed.path == "/api/export.csv":
                qs = parse_qs(parsed.query)
                status = qs.get("status", [""])[0] or None
                self._send_csv(repo.export_contacts_csv_text(status=status))
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            payload = self._read_json()
            if parsed.path == "/api/migrate":
                self._json(lambda: {"applied": repo.db.migrate()})
                return
            if parsed.path == "/api/contacts":
                def create_contact() -> dict[str, Any]:
                    inserted, skipped = repo.upsert_contacts([payload])
                    return {"inserted": inserted, "skipped": skipped}
                self._json(create_contact)
                return
            if parsed.path == "/api/import/csv":
                def import_csv() -> dict[str, Any]:
                    contacts = parse_contacts_csv(payload.get("csv", ""), default_source=payload.get("source") or "csv_import")
                    inserted, skipped = repo.upsert_contacts(contacts)
                    return {"parsed": len(contacts), "inserted": inserted, "skipped": skipped}
                self._json(import_csv)
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
                self._json(lambda: {"result": SourcingService(config, repo).source(criteria, int(payload.get("limit", 25)))})
                return
            if parsed.path == "/api/enrich":
                self._json(lambda: _result("enriched", EnrichmentService(config, repo).enrich(int(payload.get("limit", 25)))))
                return
            if parsed.path == "/api/social-enrich":
                self._json(lambda: _result("social_enriched", SocialEnrichmentService(config, repo).enrich(int(payload.get("limit", 25)))))
                return
            if parsed.path == "/api/queue":
                self._json(lambda: {"queued": QueueService(repo).queue(int(payload.get("limit", 25)))})
                return
            if parsed.path == "/api/send":
                self._json(lambda: {"sent": OutreachService(config, repo).send_due(int(payload.get("limit", 25)))})
                return
            if parsed.path == "/api/scheduler":
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
                    repo.mark_status(int(payload["contact_id"]), payload["status"], notes=payload.get("notes"))
                    return {"ok": True}
                self._json(mark)
                return
            if parsed.path == "/api/lifecycle":
                def lifecycle() -> dict[str, Any]:
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
                self._json(lambda: {"insights": ProfileAgentService(config, repo).summarize(int(payload["contact_id"]))})
                return
            if parsed.path == "/api/lifecycle-activity":
                def activity() -> dict[str, Any]:
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
                self._json(lambda: PersonalizedEmailService(config, repo).draft(
                    int(payload["contact_id"]),
                    mode=payload.get("mode") or "ai",
                    custom_subject=payload.get("subject"),
                    custom_body=payload.get("body"),
                ))
                return
            if parsed.path == "/api/send-custom":
                self._json(lambda: PersonalizedEmailService(config, repo).send(
                    int(payload["contact_id"]),
                    subject=payload.get("subject") or "",
                    body=payload.get("body") or "",
                    mode=payload.get("mode") or "custom",
                ))
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
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length == 0:
                self._raw_body = b""
                return {}
            self._raw_body = self.rfile.read(length)
            return json.loads(self._raw_body.decode("utf-8"))

        def _json(self, fn) -> None:
            try:
                self._send_json({"ok": True, "data": fn()})
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=500)

        def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False, default=_json_default).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
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
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_pixel(self) -> None:
            body = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            self.send_response(200)
            self.send_header("Content-Type", "image/gif")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
