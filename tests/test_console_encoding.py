"""Windows console must not crash on emoji / non-GBK glyphs."""

from __future__ import annotations

from harness.__main__ import _cprint, _configure_stdio, _make_event_printer
from harness.loop import Event


def test_cprint_survives_unicode_encode_error(monkeypatch):
    _configure_stdio()
    # Should not raise even if underlying print is hostile.
    _cprint("[dim]env: Windows · cwd=D:\\test[/dim]")
    _cprint("think: 你好 🖥 💾")


def test_event_printer_env_does_not_raise():
    printer = _make_event_printer(show_usage=False)
    printer(Event("env", {"env": {"family": "Windows", "cwd": "D:\\x"}}))
    printer(Event("tool_call", {"name": "read_file", "args": "{}", "turn": 1}))
    printer(
        Event(
            "tool_result",
            {"name": "read_file", "result": {"success": True, "content": "ok"}, "turn": 1},
        )
    )
