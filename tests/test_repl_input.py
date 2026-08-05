"""Multi-line REPL paste / fence assembly."""

from __future__ import annotations

from harness.repl_input import assemble_repl_message, drain_paste_lines, read_repl_message


def test_assemble_joins_paste_lines():
    msg = assemble_repl_message(
        "第一行",
        continuation_lines=["第二行", "第三行", ""],
    )
    assert msg == "第一行\n第二行\n第三行"


def test_drain_paste_lines_reads_while_pending():
    queue = ["line2\n", "line3\n"]

    def has_pending() -> bool:
        return bool(queue)

    def readline() -> str:
        return queue.pop(0)

    lines = drain_paste_lines(
        readline=readline,
        has_pending=has_pending,
        sleep=lambda _t: None,
        settle_s=0.01,
        idle_s=0.01,
    )
    assert lines == ["line2", "line3"]


def test_read_repl_message_paste_drain():
    queue_extra = ["工作区是 dawnsight\n", "不要改代码\n"]

    def prompt() -> str:
        return "帮我查模擎"

    def has_pending() -> bool:
        return bool(queue_extra)

    def readline() -> str:
        return queue_extra.pop(0)

    msg = read_repl_message(
        prompt_line=prompt,
        readline=readline,
        has_pending=has_pending,
        sleep=lambda _t: None,
    )
    assert "帮我查模擎" in msg
    assert "工作区是 dawnsight" in msg
    assert "不要改代码" in msg
    assert msg.count("\n") == 2


def test_read_repl_message_paste_command():
    lines = iter(["第一句要求", "第二句要求", "---"])

    def prompt() -> str:
        return "/paste"

    def cont() -> str:
        return next(lines)

    msg = read_repl_message(
        prompt_line=prompt,
        cont_prompt_line=cont,
        has_pending=lambda: False,
        sleep=lambda _t: None,
    )
    assert msg == "第一句要求\n第二句要求"


def test_read_repl_message_fence():
    lines = iter(["aaa", "bbb", '"""'])

    def prompt() -> str:
        return '"""'

    def cont() -> str:
        return next(lines)

    msg = read_repl_message(
        prompt_line=prompt,
        cont_prompt_line=cont,
        has_pending=lambda: False,
        sleep=lambda _t: None,
    )
    assert msg == "aaa\nbbb"
