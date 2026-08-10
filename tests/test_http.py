import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

import sales_automation.http as http_module
from sales_automation.http import HttpClient


def test_non_retryable_http_400_fails_after_one_attempt(monkeypatch):
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise HTTPError(
            "https://example.test",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error_code":"NO_RESULTS"}'),
        )

    monkeypatch.setattr(http_module, "urlopen", fail)

    with pytest.raises(RuntimeError, match="NO_RESULTS"):
        HttpClient(retries=3).request("GET", "https://example.test")

    assert calls == 1


def test_ai_investment_feishu_base_is_blocked_before_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("blocked URL must not reach the network")

    monkeypatch.setattr(http_module, "urlopen", fail)

    with pytest.raises(RuntimeError, match="AI investment Feishu Base"):
        HttpClient().request(
            "GET",
            "https://ncnqnih15n0h.feishu.cn/base/CpnybxXoGasunts8O4UckKFyn5b",
        )


def test_percent_encoded_blocked_token_is_rejected_before_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("blocked URL must not reach the network")

    monkeypatch.setattr(http_module, "urlopen", fail)

    with pytest.raises(RuntimeError, match="AI investment Feishu Base"):
        http_module.safe_urlopen(
            "https://example.test/base/%43pnybxXoGasunts8O4UckKFyn5b"
        )


def test_redirect_to_blocked_token_is_rejected_before_second_request():
    requested_paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested_paths.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/base/CpnybxXoGasunts8O4UckKFyn5b")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(RuntimeError, match="AI investment Feishu Base"):
            http_module.safe_urlopen(
                f"http://127.0.0.1:{server.server_port}/start",
                timeout=2,
            )
        assert requested_paths == ["/start"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
