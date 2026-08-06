"""secret_vault_search / secret_vault_get tools (mocked HTTP)."""

from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

from harness.tools.builtins import (
    _secret_vault_get_impl,
    _secret_vault_search_impl,
    set_secret_vault_config,
)


class _Resp:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._buf = BytesIO(raw)
        self.headers = {"Content-Type": "application/json"}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_secret_vault_requires_token():
    set_secret_vault_config(url="https://mqeng.com", token="")
    out = _secret_vault_search_impl("zyos")
    assert out["success"] is False
    assert "TOKEN" in out["error"]


def test_secret_vault_search_unwraps_code_payload(monkeypatch):
    set_secret_vault_config(url="https://mqeng.com", token="t" * 32)

    def fake_urlopen(req, timeout=0):
        return _Resp(
            {
                "code": {
                    "query": "zyos",
                    "count": 1,
                    "items": [
                        {
                            "secret_key": "zyos-local-frontend",
                            "name": "zyos 本地",
                            "category": "platform",
                            "environment": "local",
                        }
                    ],
                },
                "msg": "",
                "data": None,
            }
        )

    monkeypatch.setattr("harness.tools.builtins.urlopen", fake_urlopen)
    out = _secret_vault_search_impl("zyos")
    assert out["success"] is True
    assert out["count"] == 1
    assert out["items"][0]["secret_key"] == "zyos-local-frontend"


def test_secret_vault_get_payload(monkeypatch):
    set_secret_vault_config(url="https://mqeng.com", token="t" * 32)

    def fake_urlopen(req, timeout=0):
        headers = {str(k).lower(): v for k, v in req.headers.items()}
        assert "x-secret-vault-token" in headers
        return _Resp(
            {
                "code": {
                    "secret_key": "zyos-local-frontend",
                    "name": "zyos",
                    "category": "platform",
                    "environment": "local",
                    "payload": {"url": "http://localhost:8080", "user": "a"},
                },
                "msg": "",
                "data": None,
            }
        )

    monkeypatch.setattr("harness.tools.builtins.urlopen", fake_urlopen)
    out = _secret_vault_get_impl("zyos-local-frontend")
    assert out["success"] is True
    assert out["payload"]["url"] == "http://localhost:8080"


def test_secret_vault_http_error(monkeypatch):
    set_secret_vault_config(url="https://mqeng.com", token="t" * 32)

    def fake_urlopen(req, timeout=0):
        raise HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=BytesIO(b"{}"))

    monkeypatch.setattr("harness.tools.builtins.urlopen", fake_urlopen)
    out = _secret_vault_search_impl("zyos")
    assert out["success"] is False
    assert out.get("status") == 401
