import io
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
