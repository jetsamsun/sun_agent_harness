"""Browser tools (mocked session; no real Chromium required)."""

from __future__ import annotations

from types import SimpleNamespace

import harness.tools.browser_tools as bt
from harness import browser_session as bs
from harness.tools import registry
from harness.tools.builtins import set_llm_config


def test_browser_tools_registered():
    for name in (
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_press",
        "browser_wait",
        "browser_screenshot",
        "browser_close",
        "analyze_image",
    ):
        assert registry.get(name) is not None


def test_browser_open_fill_secret(monkeypatch):
    calls: list[tuple] = []

    class FakeSession:
        open = True

        def goto(self, url: str):
            calls.append(("goto", url))
            return SimpleNamespace(url=url, title="t")

        def fill(self, selector: str, text: str):
            calls.append(("fill", selector, text))
            return SimpleNamespace(url="http://x/", title="t")

        def info(self):
            return SimpleNamespace(url="http://x/", title="t")

    monkeypatch.setattr(bt, "get_browser_session", lambda: FakeSession())
    out = bt.browser_open("http://localhost:7891/")
    assert out["success"] is True
    filled = bt.browser_fill("input[name=password]", "secret-pass", secret=True)
    assert filled["success"] is True
    assert filled["value"] == "[redacted]"
    assert "secret-pass" not in str(filled)


def test_analyze_image_local(tmp_path, monkeypatch):
    # Minimal 1x1 PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    img = tmp_path / "a.png"
    img.write_bytes(png)
    monkeypatch.chdir(tmp_path)
    from harness.workspace import set_workspace_root

    set_workspace_root(tmp_path)
    set_llm_config(
        api_key="k",
        base_url="http://example.test/v1",
        model="m",
        vision_model="vision-m",
    )

    class Resp:
        status = 200
        headers = {"Content-Type": "application/json"}

        def read(self, n=-1):
            import json

            return json.dumps(
                {"choices": [{"message": {"content": "这是登录页"}}]}
            ).encode()

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("harness.tools.builtins.urlopen", lambda *a, **k: Resp())
    from harness.tools.builtins import _analyze_image_impl

    out = _analyze_image_impl(path="a.png", question="这是什么")
    assert out["success"] is True
    assert "登录" in out["analysis"]


def test_browser_disabled(monkeypatch):
    bs.set_browser_config(enabled=False)
    monkeypatch.setattr(bs, "_SESSION", bs.BrowserSession())
    try:
        bs.get_browser_session().ensure()
        assert False, "expected error"
    except bs.BrowserSessionError as exc:
        assert "disabled" in str(exc).lower()
    finally:
        bs.set_browser_config(enabled=True)
