"""session_search tool against fakeredis transcripts."""

from __future__ import annotations

import fakeredis

from harness.session_store import SessionStore
from harness.tools.builtins import (
    _session_search_impl,
    set_session_store,
)


def _store() -> SessionStore:
    return SessionStore(fakeredis.FakeRedis(decode_responses=True), prefix="sun_test")


def test_session_search_unavailable_without_store():
    set_session_store(None)
    out = _session_search_impl(query="秘密")
    assert out["success"] is False
    assert out["count"] == 0
    assert "Redis" in out["error"]


def test_session_search_finds_secret_across_sessions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = _store()
    set_session_store(store)
    store.save(
        session_id="20260806-aaaa",
        cwd=str(tmp_path),
        title="秘密",
        model="m",
        user_turns=1,
        status="done",
        messages=[],
        todos=[],
        plan=None,
        transcript=[
            {"role": "user", "content": "告诉你一个秘密，我昨天不小心摔倒了"},
            {"role": "assistant", "content": "好的，我不会告诉别人。"},
        ],
    )
    store.save(
        session_id="20260806-bbbb",
        cwd=str(tmp_path),
        title="别的",
        model="m",
        user_turns=1,
        status="done",
        messages=[],
        todos=[],
        plan=None,
        transcript=[
            {"role": "user", "content": "今天天气如何"},
            {"role": "assistant", "content": "深圳多云"},
        ],
    )

    hit = _session_search_impl(query="秘密")
    assert hit["success"] is True
    assert hit["count"] >= 1
    assert any("摔倒" in m["excerpt"] for m in hit["matches"])

    empty_q = _session_search_impl(query="")
    assert empty_q["success"] is True
    assert empty_q["count"] >= 1
    assert all(m["role"] == "user" for m in empty_q["matches"])


def test_session_search_respects_cwd(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(tmp_path)
    store = _store()
    set_session_store(store)
    store.save(
        session_id="20260806-cccc",
        cwd=str(other),
        title="别处",
        model="m",
        user_turns=1,
        status="done",
        messages=[],
        todos=[],
        plan=None,
        transcript=[{"role": "user", "content": "秘密只在别处"}],
    )
    out = _session_search_impl(query="秘密")
    assert out["success"] is True
    assert out["count"] == 0
