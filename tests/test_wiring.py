"""Stage-1 wiring tests that run without any API key.

These verify the tool layer, safety gate, and schema generation — the parts
that don't need a live LLM. The full four-case acceptance suite (which calls
the model) lives in test_acceptance.py and is skipped unless SUN_API_KEY is set.
"""

from __future__ import annotations

from harness.config import Settings
from harness.safety import assess_command
from harness.tools import ToolExecutor, registry


def test_all_builtin_tools_registered():
    names = {t.name for t in registry.all()}
    assert {"run_shell", "read_file", "write_file", "finish"} <= names


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
    settings = Settings(require_confirmation=False)
    ex = ToolExecutor(registry, settings)
    target = tmp_path / "note.txt"
    w = ex.execute("write_file", f'{{"path": "{target.as_posix()}", "content": "hi"}}')
    assert w["success"] is True
    r = ex.execute("read_file", f'{{"path": "{target.as_posix()}"}}')
    assert r["success"] is True
    assert "hi" in r["content"]


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
    client.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "finish"}}],
    )
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

    msg = client.chat([{"role": "user", "content": "hi"}])
    assert msg.content == "ok"
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
