"""Tests for fetch_url / search_web (HTML → text, cache, scheme guard)."""

from __future__ import annotations

from harness.config import Settings
from harness.tools.builtins import (
    _html_to_text,
    _normalize_url,
    _unwrap_ddg_href,
    clear_fetch_cache,
    registry,
)
from harness.tools.executor import ToolExecutor

settings = Settings(api_key="test")


class _FakeResp:
    def __init__(
        self,
        body: bytes,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        final_url: str = "https://example.com/page",
    ):
        self.status = status
        self._body = body
        self.headers = {"Content-Type": content_type}
        self._final_url = final_url

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._final_url

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def setup_function() -> None:
    clear_fetch_cache()


def test_html_to_text_strips_script_and_keeps_title():
    html = (
        "<html><head><title>问候</title>"
        "<style>.x{color:red}</style></head>"
        "<body><h1>Hello</h1><p>世界</p>"
        "<script>evil()</script></body></html>"
    )
    title, text = _html_to_text(html)
    assert title == "问候"
    assert "Hello" in text
    assert "世界" in text
    assert "evil" not in text
    assert "color:red" not in text


def test_normalize_url_drops_fragment_and_trailing_slash():
    assert _normalize_url("HTTPS://Example.com/docs/#sec") == "https://example.com/docs"
    assert _normalize_url("https://example.com/docs/") == "https://example.com/docs"


def test_unwrap_ddg_href():
    href = (
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fapi-docs.deepseek.com%2Fzh-cn%2Fguides%2Fx"
        "&rut=abc"
    )
    assert _unwrap_ddg_href(href) == "https://api-docs.deepseek.com/zh-cn/guides/x"


def test_fetch_url_rejects_non_http():
    ex = ToolExecutor(registry, settings)
    result = ex.execute("fetch_url", '{"url": "file:///etc/passwd"}')
    assert result["success"] is False
    assert "http" in result["error"].lower()


def test_fetch_url_extracts_page(monkeypatch):
    html = (
        b"<html><head><title>Demo</title></head>"
        b"<body><p>Alpha beta</p></body></html>"
    )
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):  # noqa: ARG001
        calls["n"] += 1
        return _FakeResp(html)

    monkeypatch.setattr("harness.tools.builtins.urlopen", fake_urlopen)
    ex = ToolExecutor(registry, settings)
    result = ex.execute("fetch_url", '{"url": "https://example.com/page"}')
    assert result["success"] is True
    assert result["title"] == "Demo"
    assert "Alpha beta" in result["text"]
    assert result["status"] == 200
    assert result["cached"] is False
    assert calls["n"] == 1


def test_fetch_url_reuses_cache_for_same_url(monkeypatch):
    html = b"<html><head><title>Demo</title></head><body><p>Cached</p></body></html>"
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):  # noqa: ARG001
        calls["n"] += 1
        return _FakeResp(html)

    monkeypatch.setattr("harness.tools.builtins.urlopen", fake_urlopen)
    ex = ToolExecutor(registry, settings)
    r1 = ex.execute("fetch_url", '{"url": "https://example.com/page#a"}')
    r2 = ex.execute("fetch_url", '{"url": "https://example.com/page/"}')
    assert r1["cached"] is False
    assert r2["cached"] is True
    assert "Cached" in r2["text"]
    assert calls["n"] == 1


def test_fetch_url_plain_json(monkeypatch):
    body = b'{"ok": true, "n": 1}'

    def fake_urlopen(req, timeout=30):  # noqa: ARG001
        return _FakeResp(body, content_type="application/json")

    monkeypatch.setattr("harness.tools.builtins.urlopen", fake_urlopen)
    ex = ToolExecutor(registry, settings)
    result = ex.execute("fetch_url", '{"url": "https://example.com/api", "max_chars": 500}')
    assert result["success"] is True
    assert '"ok": true' in result["text"]
    assert result["title"] == ""


def test_search_web_parses_ddg_html(monkeypatch):
    html = b"""
    <html><body>
      <a rel="nofollow" class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.com%2Fresp">
        Responses API Guide
      </a>
      <a class="result__snippet">Official docs for Responses API.</a>
      <a rel="nofollow" class="result__a" href="https://other.example/x">Other</a>
      <div class="result__snippet">Secondary hit.</div>
    </body></html>
    """

    def fake_urlopen(req, timeout=30):  # noqa: ARG001
        return _FakeResp(html, final_url="https://html.duckduckgo.com/html/?q=test")

    monkeypatch.setattr("harness.tools.builtins.urlopen", fake_urlopen)
    ex = ToolExecutor(registry, settings)
    result = ex.execute("search_web", '{"query": "Responses API DeepSeek", "max_results": 5}')
    assert result["success"] is True
    assert result["count"] == 2
    assert result["results"][0]["url"] == "https://docs.example.com/resp"
    assert result["results"][0]["title"] == "Responses API Guide"
    assert "Official docs" in result["results"][0]["snippet"]
