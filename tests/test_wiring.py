"""Stage-1 wiring tests that run without any API key.

These verify the tool layer, safety gate, and schema generation — the parts
that don't need a live LLM. The full four-case acceptance suite (which calls
the model) lives in test_acceptance.py and is skipped unless SUN_API_KEY is set.
"""

from __future__ import annotations

import json

from harness.config import Settings
from harness.safety import assess_command
from harness.tools import (
    ToolExecutor,
    registry,
    reset_planning_state,
    set_ask_fn,
    set_confirm_edits,
    set_edit_confirm_fn,
    set_plan_confirm_fn,
)
from harness.workspace import set_workspace_root


def test_all_builtin_tools_registered():
    names = {t.name for t in registry.all()}
    assert {
        "run_shell",
        "read_file",
        "write_file",
        "edit_file",
        "search_files",
        "find_files",
        "list_dir",
        "check_syntax",
        "run_tests",
        "run_lint",
        "ask_user",
        "propose_plan",
        "todo_write",
        "git_checkpoint",
        "git_rollback",
        "git_commit",
        "finish",
    } <= names


def test_tool_schema_shape():
    schema = registry.get("run_shell").to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "run_shell"
    assert "command" in schema["function"]["parameters"]["properties"]
    assert "command" in schema["function"]["parameters"]["required"]


def test_safety_flags_rm_rf():
    assert assess_command("rm -rf /tmp/foo") is not None
    assert assess_command("sudo apt install x") is not None
    assert assess_command("ls -la") is None


def test_safety_allows_stderr_to_devnull():
    # Regression: `2>/dev/null` and `/dev/null` sinks must NOT be flagged.
    assert assess_command("find . -type f 2>/dev/null | wc -l") is None
    assert assess_command("grep foo bar.txt 2>&1") is None
    assert assess_command("echo hi > /dev/null") is None


def test_safety_flags_real_write_to_system_path():
    assert assess_command("echo x > /etc/passwd") is not None
    assert assess_command("cat foo >> /usr/local/bin/thing") is not None
    assert assess_command("echo x > /dev/sda") is not None


def test_executor_runs_shell():
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    result = ex.execute("run_shell", '{"command": "echo hello"}')
    assert result["success"] is True
    assert "hello" in result["stdout"]


def test_executor_write_then_read(tmp_path):
    set_workspace_root(tmp_path)
    set_confirm_edits(False)
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    target = tmp_path / "note.txt"
    w = ex.execute("write_file", f'{{"path": "{target.as_posix()}", "content": "hi"}}')
    assert w["success"] is True
    assert "diff" in w
    r = ex.execute("read_file", f'{{"path": "{target.as_posix()}"}}')
    assert r["success"] is True
    assert "hi" in r["content"]


def test_read_file_offset_limit(tmp_path):
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    target = tmp_path / "lines.txt"
    target.write_text("a\nb\nc\nd\n", encoding="utf-8")
    r = ex.execute(
        "read_file",
        f'{{"path": "{target.as_posix()}", "offset": 2, "limit": 2}}',
    )
    assert r["success"] is True
    assert r["start_line"] == 2
    assert r["end_line"] == 3
    assert "2|b" in r["content"]
    assert "3|c" in r["content"]
    assert "1|a" not in r["content"]

    # Models often pass numbers as JSON strings.
    r2 = ex.execute(
        "read_file",
        f'{{"path": "{target.as_posix()}", "offset": "2", "limit": "2"}}',
    )
    assert r2["success"] is True
    assert "2|b" in r2["content"]


def test_edit_file_unique_replace(tmp_path):
    set_workspace_root(tmp_path)
    set_confirm_edits(False)
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    target = tmp_path / "app.py"
    target.write_text("x = 1\ny = 1\n", encoding="utf-8")
    path = target.as_posix()
    ok = ex.execute(
        "edit_file",
        f'{{"path": "{path}", "old_text": "x = 1", "new_text": "x = 2"}}',
    )
    assert ok["success"] is True
    assert target.read_text(encoding="utf-8") == "x = 2\ny = 1\n"

    bad = ex.execute(
        "edit_file",
        f'{{"path": "{path}", "old_text": " = ", "new_text": "="}}',
    )
    assert bad["success"] is False
    assert "matched" in bad["error"].lower() or "times" in bad["error"].lower()


def test_search_find_list_dir(tmp_path):
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("alpha_marker = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "b.txt").write_text("nope\n", encoding="utf-8")
    root = tmp_path.as_posix()

    found = ex.execute("find_files", f'{{"pattern": "*.py", "path": "{root}"}}')
    assert found["success"] is True
    assert found["count"] >= 1
    assert any(p.endswith("a.py") for p in found["files"])

    searched = ex.execute(
        "search_files",
        f'{{"pattern": "alpha_marker", "path": "{root}", "glob": "*.py"}}',
    )
    assert searched["success"] is True
    assert searched["match_count"] >= 1

    listed = ex.execute("list_dir", f'{{"path": "{root}"}}')
    assert listed["success"] is True
    names = {e["name"] for e in listed["entries"]}
    assert "pkg" in names


def test_executor_blocks_dangerous_without_confirm_channel():
    settings = Settings(require_confirmation=True)
    ex = ToolExecutor(registry, settings, confirm_fn=None)
    result = ex.execute("run_shell", '{"command": "rm -rf /tmp/x"}')
    assert result["success"] is False
    assert "dangerous" in result["error"].lower()


def test_executor_confirm_declined():
    settings = Settings(require_confirmation=True)
    ex = ToolExecutor(registry, settings, confirm_fn=lambda cmd, reason: False)
    result = ex.execute("run_shell", '{"command": "rm -rf /tmp/x"}')
    assert result["success"] is False


def test_finish_tool_signals_completion():
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    result = ex.execute("finish", '{"summary": "done"}')
    assert result["finished"] is True
    assert result["summary"] == "done"


def test_check_syntax_python(tmp_path):
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    good = tmp_path / "ok.py"
    good.write_text("x = 1\n", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("def (\n", encoding="utf-8")

    ok = ex.execute("check_syntax", f'{{"path": "{good.as_posix()}"}}')
    assert ok["passed"] is True
    fail = ex.execute("check_syntax", f'{{"path": "{bad.as_posix()}"}}')
    assert fail["passed"] is False


def test_run_tests_autodetect_and_pass(tmp_path, monkeypatch):
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_mini.py").write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
    result = ex.execute("run_tests", "{}")
    assert result["passed"] is True
    assert "pytest" in result["command"]


def test_ask_user_and_propose_plan_and_todos(monkeypatch):
    reset_planning_state()
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    monkeypatch.setattr("harness.tools.builtins.sys.stdin.isatty", lambda: True)
    set_ask_fn(lambda q: f"answer-to:{q}")
    set_plan_confirm_fn(lambda title, steps: (True, ""))

    asked = ex.execute("ask_user", '{"question": "target lang?"}')
    assert asked["success"] is True
    assert asked["answer"] == "answer-to:target lang?"

    steps = [
        {"id": "1", "title": "add util", "acceptance": "pytest green"},
        {"id": "2", "title": "docs", "acceptance": "README mentions util"},
    ]
    plan = ex.execute(
        "propose_plan",
        json.dumps({"title": "Ship util", "steps_json": json.dumps(steps)}),
    )
    assert plan["success"] is True
    assert plan["approved"] is True
    assert len(plan["steps"]) == 2

    items = [
        {"id": "1", "content": "add util", "status": "in_progress"},
        {"id": "2", "content": "docs", "status": "pending"},
    ]
    todos = ex.execute("todo_write", json.dumps({"items_json": json.dumps(items)}))
    assert todos["success"] is True
    assert todos["count"] == 2
    assert todos["todos"][0]["status"] == "in_progress"


def test_propose_plan_rejected_note(monkeypatch):
    reset_planning_state()
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    monkeypatch.setattr("harness.tools.builtins.sys.stdin.isatty", lambda: True)
    set_plan_confirm_fn(lambda title, steps: (False, "too big"))
    step = [{"id": "1", "title": "a", "acceptance": "b"}]
    plan = ex.execute(
        "propose_plan",
        json.dumps({"title": "X", "steps_json": json.dumps(step)}),
    )
    assert plan["success"] is True
    assert plan["approved"] is False
    assert plan["note"] == "too big"


def _fake_msg_classes():
    class _Fn:
        def __init__(self, name: str, arguments: str) -> None:
            self.name = name
            self.arguments = arguments

    class _TC:
        def __init__(self, id: str, name: str, arguments: str) -> None:
            self.id = id
            self.function = _Fn(name, arguments)

    class _Msg:
        def __init__(self, tool_calls, content: str = "") -> None:
            self.content = content
            self.tool_calls = tool_calls

    return _Fn, _TC, _Msg


def _as_chat(message, *, prompt: int = 1, completion: int = 1):
    from harness.llm import ChatResult

    return ChatResult(
        message=message,
        prompt_tokens=prompt,
        completion_tokens=completion,
        latency_ms=1.0,
    )


def test_loop_refuses_finish_while_tests_red(monkeypatch):
    """After a failing run_tests, finish must not end the loop."""
    from harness.config import Settings
    from harness.llm import LLMClient
    from harness.loop import AgentLoop

    _, _TC, _Msg = _fake_msg_classes()
    settings = Settings(
        api_key="x", max_turns=3, require_confirmation=False, streaming=False
    )
    ex = ToolExecutor(registry, settings)
    client = LLMClient(settings)

    calls = {"n": 0}
    fail_cmd = '{"command": "python -c \\"import sys; sys.exit(1)\\""}'

    def fake_chat(_messages, tools=None, on_delta=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _as_chat(_Msg([_TC("1", "run_tests", fail_cmd)]))
        if calls["n"] == 2:
            return _as_chat(_Msg([_TC("2", "finish", '{"summary": "done anyway"}')]))
        return _as_chat(_Msg(None))

    monkeypatch.setattr(client, "chat", fake_chat)
    loop = AgentLoop(client, registry, ex, settings)
    out = loop.run("task")
    assert calls["n"] >= 3
    assert out != "done anyway"


def test_session_reuses_context_across_runs(monkeypatch):
    """REPL session=True must keep prior user turns in the next LLM call."""
    from harness.config import Settings
    from harness.llm import LLMClient
    from harness.loop import AgentLoop

    _, _TC, _Msg = _fake_msg_classes()
    settings = Settings(
        api_key="x", max_turns=2, require_confirmation=False, streaming=False
    )
    ex = ToolExecutor(registry, settings)
    client = LLMClient(settings)
    seen: list[list] = []

    def fake_chat(messages, tools=None, on_delta=None):
        seen.append(list(messages))
        return _as_chat(_Msg(None, content="ok"))

    monkeypatch.setattr(client, "chat", fake_chat)
    loop = AgentLoop(client, registry, ex, settings)
    loop.run("remember: apple", session=True)
    loop.run("what did I say?", session=True)

    assert len(seen) == 2
    users = [m["content"] for m in seen[1] if m.get("role") == "user"]
    assert "remember: apple" in users
    assert "what did I say?" in users
    assert loop.session_context() is not None
    assert loop.session_context().user_turns() == 2


def test_oneshot_run_does_not_reuse_session(monkeypatch):
    from harness.config import Settings
    from harness.llm import LLMClient
    from harness.loop import AgentLoop

    _, _TC, _Msg = _fake_msg_classes()
    settings = Settings(
        api_key="x", max_turns=2, require_confirmation=False, streaming=False
    )
    ex = ToolExecutor(registry, settings)
    client = LLMClient(settings)
    seen: list[list] = []

    def fake_chat(messages, tools=None, on_delta=None):
        seen.append(list(messages))
        return _as_chat(_Msg(None, content="ok"))

    monkeypatch.setattr(client, "chat", fake_chat)
    loop = AgentLoop(client, registry, ex, settings)
    loop.run("first")
    loop.run("second")
    users2 = [m["content"] for m in seen[1] if m.get("role") == "user"]
    assert users2 == ["second"]
    assert loop.session_context() is None


def test_clear_session_drops_history(monkeypatch):
    from harness.config import Settings
    from harness.llm import LLMClient
    from harness.loop import AgentLoop

    _, _TC, _Msg = _fake_msg_classes()
    settings = Settings(
        api_key="x", max_turns=2, require_confirmation=False, streaming=False
    )
    ex = ToolExecutor(registry, settings)
    client = LLMClient(settings)

    def _ok_chat(messages, tools=None, on_delta=None):
        return _as_chat(_Msg(None, content="ok"))

    monkeypatch.setattr(client, "chat", _ok_chat)
    loop = AgentLoop(client, registry, ex, settings)
    loop.run("keep me", session=True)
    loop.clear_session()
    assert loop.session_context() is None
    seen: list = []

    def fake_chat(messages, tools=None, on_delta=None):
        seen.append(messages)
        return _as_chat(_Msg(None, content="ok"))

    monkeypatch.setattr(client, "chat", fake_chat)
    loop.run("fresh", session=True)
    users = [m["content"] for m in seen[0] if m.get("role") == "user"]
    assert users == ["fresh"]


def test_clip_text_keeps_head_and_tail():
    from harness.context import clip_text

    text = "A" * 100 + "MID" + "B" * 100
    out = clip_text(text, limit=80)
    assert out.startswith("A")
    assert out.endswith("B")
    assert "truncated" in out
    assert "MID" not in out
    assert len(out) < len(text)


def test_workspace_blocks_path_escape(tmp_path):
    set_workspace_root(tmp_path)
    set_confirm_edits(False)
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    outside = tmp_path.parent / "outside-sun.txt"
    result = ex.execute(
        "write_file",
        json.dumps({"path": str(outside), "content": "nope"}),
    )
    assert result["success"] is False
    assert "workspace" in result["error"].lower()
    assert not outside.exists()


def test_edit_confirm_declined(tmp_path):
    set_workspace_root(tmp_path)
    set_confirm_edits(True)
    set_edit_confirm_fn(lambda _path, _diff: False)
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    target = tmp_path / "x.txt"
    target.write_text("old\n", encoding="utf-8")
    result = ex.execute(
        "write_file",
        json.dumps({"path": str(target), "content": "new\n"}),
    )
    assert result["success"] is False
    assert "declined" in result["error"].lower()
    assert target.read_text(encoding="utf-8") == "old\n"
    set_confirm_edits(False)
    set_edit_confirm_fn(None)


def test_git_checkpoint_rollback_commit(tmp_path):
    import subprocess

    from harness.gitops import reset_git_state, set_auto_git_checkpoint

    set_workspace_root(tmp_path)
    set_confirm_edits(False)
    set_auto_git_checkpoint(True)
    reset_planning_state()
    reset_git_state()

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "sun@test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Sun Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed.txt"
    seed.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)

    # Dirty tree before edit → auto checkpoint should commit it, then write.
    seed.write_text("v1-dirty\n", encoding="utf-8")
    w = ex.execute(
        "write_file",
        json.dumps({"path": str(tmp_path / "app.txt"), "content": "hello\n"}),
    )
    assert w["success"] is True
    assert w.get("checkpoint", {}).get("success") is True
    sha = w["checkpoint"]["sha"]
    assert (tmp_path / "app.txt").read_text(encoding="utf-8") == "hello\n"

    # Further bad edit, then rollback to checkpoint (pre-app.txt? or after dirty commit)
    ex.execute(
        "write_file",
        json.dumps({"path": str(tmp_path / "app.txt"), "content": "bad\n"}),
    )
    rb = ex.execute("git_rollback", json.dumps({"sha": sha}))
    assert rb["success"] is True
    # After hard reset to checkpoint (dirty commit of seed), app.txt may or may not exist
    # depending on whether checkpoint included app.txt. Auto checkpoint runs BEFORE write,
    # so sha is pre-app.txt; rollback removes app.txt.
    assert not (tmp_path / "app.txt").exists()

    # Recreate and commit via tool
    ex.execute(
        "write_file",
        json.dumps({"path": str(tmp_path / "app.txt"), "content": "final\n"}),
    )
    c = ex.execute("git_commit", json.dumps({"message": "sun: save work"}))
    assert c["success"] is True
    assert c.get("sha")

    set_auto_git_checkpoint(False)
    set_workspace_root(None)


def test_resolve_reasoning_effort_auto_none_for_terra_with_tools():
    from harness.llm import resolve_reasoning_effort

    tools = [{"type": "function", "function": {"name": "finish"}}]
    assert resolve_reasoning_effort("gpt-5.6-terra", tools, "") == "none"
    assert resolve_reasoning_effort("gpt-5.6-terra", None, "") is None
    assert resolve_reasoning_effort("gpt-4o-mini", tools, "") is None
    assert resolve_reasoning_effort("gpt-5.6-terra", tools, "low") == "low"


def test_llm_passes_reasoning_effort_none_for_terra_tools(monkeypatch):
    from harness.config import Settings
    from harness.llm import LLMClient

    client = LLMClient(Settings(api_key="x", model="gpt-5.6-terra", max_retries=1))
    seen: dict = {}

    class _Msg:
        content = "ok"
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_create(**kwargs):
        seen.update(kwargs)
        return _Resp()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    result = client.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "finish"}}],
    )
    assert result.message.content == "ok"
    assert seen.get("reasoning_effort") == "none"
    assert seen.get("tool_choice") == "auto"


def test_llm_retries_transient_then_succeeds(monkeypatch):
    """A transient error should be retried, not surfaced immediately."""
    import harness.llm as llm_mod
    from harness.config import Settings
    from harness.llm import LLMClient

    # Avoid real backoff sleeps in the test.
    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)

    client = LLMClient(Settings(api_key="x", max_retries=3))

    calls = {"n": 0}

    class _Msg:
        content = "ok"
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_create(**_kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise llm_mod.APITimeoutError(request=None)
        return _Resp()

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    result = client.chat([{"role": "user", "content": "hi"}])
    assert result.message.content == "ok"
    assert calls["n"] == 2  # failed once, succeeded on retry


def test_llm_gives_up_after_max_retries(monkeypatch):
    import harness.llm as llm_mod
    from harness.config import Settings
    from harness.llm import LLMClient

    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)
    client = LLMClient(Settings(api_key="x", max_retries=2))

    def always_fail(**_kwargs):
        raise llm_mod.APIConnectionError(request=None)

    monkeypatch.setattr(client._client.chat.completions, "create", always_fail)

    try:
        client.chat([{"role": "user", "content": "hi"}])
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "2 attempts" in str(exc)


def test_stream_assembler_builds_tool_calls():
    from harness.llm import _StreamAssembler

    class _FnDelta:
        def __init__(self, name=None, arguments=None):
            self.name = name
            self.arguments = arguments

    class _TcDelta:
        def __init__(self, index, id=None, name=None, arguments=None):
            self.index = index
            self.id = id
            self.function = _FnDelta(name, arguments)

    class _Delta:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    asm = _StreamAssembler()
    assert asm.feed(_Delta(content="hi ")) == "hi "
    assert asm.feed(_Delta(content="there")) == "there"
    asm.feed(_Delta(tool_calls=[_TcDelta(0, id="c1", name="finish")]))
    asm.feed(_Delta(tool_calls=[_TcDelta(0, arguments='{"summary":')]))
    asm.feed(_Delta(tool_calls=[_TcDelta(0, arguments='"ok"}')]))
    msg = asm.to_message()
    assert msg.content == "hi there"
    assert msg.tool_calls is not None
    assert msg.tool_calls[0].function.name == "finish"
    assert msg.tool_calls[0].function.arguments == '{"summary":"ok"}'


def test_usage_totals_and_cost():
    from harness.usage import UsageTotals

    u = UsageTotals(model="gpt-4o-mini")
    u.add_llm(turn=1, prompt_tokens=1_000_000, completion_tokens=0, latency_ms=10)
    u.add_tool(latency_ms=5)
    assert u.total_tokens == 1_000_000
    cost = u.estimate_cost_usd()
    assert cost is not None
    assert abs(cost - 0.15) < 1e-9
    assert "tokens" in u.summary_line()


def test_trace_sink_writes_jsonl(tmp_path):
    from harness.loop import Event
    from harness.trace import TraceSink

    path = tmp_path / "t.jsonl"
    seen: list = []
    sink = TraceSink(path, on_event=seen.append)
    sink(Event("think", {"text": "x", "turn": 1}))
    sink(Event("usage", {"total_tokens": 3}))
    sink.close()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["kind"] == "think"
    assert len(seen) == 2


def test_context_compress_folds_old_turns():
    from harness.context import Context

    ctx = Context("sys")
    for i in range(20):
        ctx.add_user(f"user goal {i} " + ("x" * 200))

        class _Fn:
            name = "run_shell"
            arguments = '{"command": "echo hi"}'

        class _TC:
            id = f"c{i}"
            function = _Fn()

        class _Msg:
            content = ""
            tool_calls = [_TC()]

        ctx.add_assistant(_Msg())
        fat = json.dumps({"success": True, "stdout": "Y" * 5000})
        ctx.add_tool_result(f"c{i}", fat)

    before = ctx.token_estimate()
    info = ctx.maybe_compress(max_tokens=800, keep_recent=6, summarize_fn=None)
    assert info is not None
    assert info["after_tokens"] < before
    assert info["dropped_messages"] > 0
    msgs = ctx.messages()
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "摘要" in msgs[1]["content"]
    # Kept window must not start with an orphan tool message.
    assert msgs[2]["role"] in {"user", "assistant"}


def test_context_compress_noop_under_budget():
    from harness.context import Context

    ctx = Context("sys")
    ctx.add_user("hi")
    assert ctx.maybe_compress(max_tokens=50_000, keep_recent=8) is None


def test_run_shell_decodes_utf8_not_locale():
    """Shell stdout must decode as UTF-8 (not Windows GBK default)."""
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    # Avoid embedding CJK in the shell command line (Windows console quoting).
    # Emit UTF-8 bytes that are invalid as GBK if mis-decoded as the locale.
    cmd = (
        "python -c \"import sys; "
        "sys.stdout.buffer.write(bytes([0xe5,0xa4,0xa9,0xe6,0xb0,0x94,0x0a])); "
        "sys.stdout.buffer.flush()\""
    )
    result = ex.execute("run_shell", json.dumps({"command": cmd}))
    assert result["success"] is True, result
    assert "天气" in result["stdout"]


def test_build_system_prompt_includes_detected_os():
    from harness.loop import build_system_prompt, detect_runtime_env

    env = detect_runtime_env()
    prompt = build_system_prompt(env)
    assert "当前运行环境" in prompt
    assert env["family"] in prompt
    assert "Linux shell" not in prompt
    assert "你是 Sun" in prompt


def test_loop_emits_usage_event(monkeypatch):
    from harness.config import Settings
    from harness.llm import LLMClient
    from harness.loop import AgentLoop, Event

    _, _TC, _Msg = _fake_msg_classes()
    settings = Settings(
        api_key="x", max_turns=2, require_confirmation=False, streaming=False
    )
    ex = ToolExecutor(registry, settings)
    client = LLMClient(settings)
    events: list[Event] = []

    def fake_chat(_messages, tools=None, on_delta=None):
        return _as_chat(_Msg(None, content="done"), prompt=11, completion=7)

    monkeypatch.setattr(client, "chat", fake_chat)
    loop = AgentLoop(client, registry, ex, settings, on_event=events.append)
    assert loop.run("hi") == "done"
    usage_events = [e for e in events if e.kind == "usage"]
    assert usage_events
    assert usage_events[-1].data["prompt_tokens"] == 11
    assert usage_events[-1].data["completion_tokens"] == 7
    assert any(e.kind == "turn" for e in events)
