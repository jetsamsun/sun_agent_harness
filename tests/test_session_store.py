"""Redis session store + resume wiring (fakeredis, no real Redis)."""

from __future__ import annotations

from pathlib import Path

import fakeredis

from harness.context import Context
from harness.session_store import SessionStore, new_session_id
from harness.tools.builtins import export_planning_state, import_planning_state


def _store() -> SessionStore:
    return SessionStore(fakeredis.FakeRedis(decode_responses=True), prefix="sun_test")


def test_new_session_id_shape():
    sid = new_session_id()
    assert len(sid) == 13  # YYYYMMDD-xxxx
    assert sid[8] == "-"


def test_save_list_resume_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = _store()
    sid = "20260805-abcd"
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "继续改登录"},
        {"role": "assistant", "content": "好"},
    ]
    transcript = list(msgs)
    meta = store.save(
        session_id=sid,
        cwd=str(tmp_path),
        title="继续改登录",
        model="m",
        user_turns=1,
        status="active",
        messages=msgs,
        todos=[{"id": "1", "content": "x", "status": "pending"}],
        plan={"title": "p", "approved": True},
        transcript=transcript,
    )
    assert meta.id == sid
    listed = store.list_sessions(cwd=str(tmp_path))
    assert len(listed) == 1
    assert listed[0].title == "继续改登录"

    # Other cwd hidden
    other = tmp_path / "other"
    other.mkdir()
    assert store.list_sessions(cwd=str(other)) == []

    state = store.get_state(sid)
    assert state is not None
    assert state["messages"][1]["content"] == "继续改登录"
    assert store.get_transcript(sid)[0]["role"] == "system"


def test_cwd_mismatch_detected_by_meta(tmp_path, monkeypatch):
    store = _store()
    sid = "20260805-eeee"
    store.save(
        session_id=sid,
        cwd=str(tmp_path / "a"),
        title="t",
        model="m",
        user_turns=0,
        status="active",
        messages=[{"role": "system", "content": "s"}],
        todos=[],
        plan=None,
        transcript=[],
    )
    meta = store.get_meta(sid)
    assert meta is not None
    monkeypatch.chdir(tmp_path)
    assert meta.cwd != str(Path.cwd().resolve())


def test_prune_deletes_cwd_sessions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = _store()
    store.save(
        session_id="20260805-1111",
        cwd=str(tmp_path),
        title="a",
        model="m",
        user_turns=1,
        status="active",
        messages=[{"role": "system", "content": "s"}],
        todos=[],
        plan=None,
        transcript=[],
    )
    deleted = store.prune(cwd=str(tmp_path))
    assert "20260805-1111" in deleted
    assert store.get_meta("20260805-1111") is None


def test_planning_export_import_roundtrip():
    import_planning_state(
        {"todos": [{"id": "1", "content": "t", "status": "in_progress"}], "plan": None}
    )
    snap = export_planning_state()
    assert snap["todos"][0]["id"] == "1"
    import_planning_state(None)
    assert export_planning_state()["todos"] == []


def test_context_from_messages_preserves_history():
    msgs = [
        {"role": "system", "content": "old"},
        {"role": "user", "content": "hi"},
    ]
    ctx = Context.from_messages(msgs)
    ctx.set_system_prompt("new sys")
    assert ctx.messages()[0]["content"] == "new sys"
    assert ctx.messages()[1]["content"] == "hi"
    assert ctx.user_turns() == 1


def test_loop_resume_restores_messages(tmp_path, monkeypatch):
    from harness.config import Settings
    from harness.llm import LLMClient
    from harness.loop import AgentLoop
    from harness.tools import ToolExecutor, registry

    monkeypatch.chdir(tmp_path)
    persona = tmp_path / "PERSONA.md"
    persona.write_text("# p\n", encoding="utf-8")
    store = _store()
    sid = "20260805-r1r1"
    store.save(
        session_id=sid,
        cwd=str(tmp_path.resolve()),
        title="resume-me",
        model="m",
        user_turns=1,
        status="active",
        messages=[
            {"role": "system", "content": "old"},
            {"role": "user", "content": "先前任务"},
            {"role": "assistant", "content": "做到一半"},
        ],
        todos=[],
        plan=None,
        transcript=[{"role": "user", "content": "先前任务"}],
    )
    settings = Settings(
        api_key="x",
        require_confirmation=False,
        streaming=False,
        persona_path=str(persona),
        sqlite_path=str(tmp_path / "long_memory.db"),
        redis_url="redis://unused",
    )
    loop = AgentLoop(
        LLMClient(settings),
        registry,
        ToolExecutor(registry, settings),
        settings,
        store=store,
    )
    assert loop.resume_session(sid) == sid
    ctx = loop.session_context()
    assert ctx is not None
    assert any(m.get("content") == "先前任务" for m in ctx.messages())
    assert loop.session_id() == sid
