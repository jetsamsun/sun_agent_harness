"""Tests for list_models / fetch_model_status."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError

from harness.tools import builtins
from harness.tools.builtins import fetch_model_status, list_models, set_llm_config


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, _n: int = -1) -> bytes:
        return self._raw

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_list_models_registered():
    assert builtins.registry.get("list_models") is not None


def test_fetch_model_status_marks_current(monkeypatch):
    set_llm_config(
        api_key="sk-test",
        base_url="http://example.com/v1",
        model="glm-5.2",
    )

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        assert req.full_url == "http://example.com/v1/models"
        assert req.get_header("Authorization") == "Bearer sk-test"
        return _FakeResp(
            {
                "data": [
                    {"id": "deepseek-v4-flash", "owned_by": "deepseek"},
                    {"id": "glm-5.2", "owned_by": "claude"},
                ]
            }
        )

    monkeypatch.setattr(builtins, "urlopen", fake_urlopen)
    out = list_models()
    assert out["success"] is True
    assert out["current_model"] == "glm-5.2"
    assert out["count"] == 2
    assert out["available_models"][0]["id"] == "glm-5.2"
    assert out["available_models"][0]["current"] is True


def test_fetch_model_status_http_error(monkeypatch):
    set_llm_config(api_key="sk-test", base_url="http://example.com/v1", model="x")

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        raise HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"nope"}'),
        )

    monkeypatch.setattr(builtins, "urlopen", fake_urlopen)
    out = fetch_model_status()
    assert out["success"] is False
    assert "401" in out["error"]


def test_fetch_model_status_requires_config():
    set_llm_config(api_key="", base_url="", model="")
    out = fetch_model_status()
    assert out["success"] is False
