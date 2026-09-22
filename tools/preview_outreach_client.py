"""Local read-only client preview using configured data, with no startup jobs."""
import argparse
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from sales_automation.config import load_config
from sales_automation.db import Database, Repository
from sales_automation.web import make_handler


def make_preview_handler(config, repo):
    handler = make_handler(config, repo)

    class ReadOnlyHandler(handler):
        def do_POST(self):
            if urlparse(self.path).path not in {"/api/login", "/api/change-password", "/api/logout"}:
                self._send_json({"ok": False, "error": "Read-only preview; changes and sends are disabled"}, status=403)
                return
            super().do_POST()

        def do_GET(self):
            if urlparse(self.path).path in {"/unsubscribe", "/track/open", "/api/admin/senders"}:
                self.send_error(404)
                return
            super().do_GET()

    return ReadOnlyHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18771)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    config.raw.setdefault("app", {})["public_base_url"] = f"http://127.0.0.1:{args.port}"
    handler = make_preview_handler(config, Repository(Database(config)))
    ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()


if __name__ == "__main__":
    main()
