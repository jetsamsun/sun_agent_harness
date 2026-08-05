"""Editable PERSONA.md loading / reload behavior."""

from __future__ import annotations

from harness.context import Context
from harness.loop import build_system_prompt
from harness.persona import (
    DEFAULT_PERSONA,
    ensure_persona_file,
    load_persona_block,
    load_persona_text,
    project_persona_path,
    resolve_persona_path,
)


def test_ensure_and_load_persona(tmp_path):
    path = tmp_path / "PERSONA.md"
    assert ensure_persona_file(path) is True
    assert ensure_persona_file(path) is False
    text = load_persona_text(path)
    assert "Sun 人格" in text
    assert text.startswith("#")


def test_project_persona_wins_over_global(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = project_persona_path(tmp_path)
    project.parent.mkdir(parents=True)
    project.write_text("# 项目人格\n只对这个目录。\n", encoding="utf-8")
    # Even if global would exist, resolve prefers project when present.
    assert resolve_persona_path("") == project
    block, path = load_persona_block("")
    assert path == project
    assert "项目人格" in block


def test_build_system_prompt_includes_persona(tmp_path):
    persona = tmp_path / "me.md"
    persona.write_text("说话像海盗。\n", encoding="utf-8")
    db = tmp_path / "long_memory.db"
    prompt = build_system_prompt(persona_path=str(persona), sqlite_path=str(db))
    assert "人格" in prompt or "长久记忆" in prompt
    assert "说话像海盗" in prompt


def test_context_set_system_prompt_reloads_persona():
    ctx = Context("system v1")
    ctx.add_user("hi")
    ctx.set_system_prompt("system v2 with new persona")
    assert ctx.messages()[0]["content"] == "system v2 with new persona"
    assert ctx.messages()[1]["role"] == "user"
    assert DEFAULT_PERSONA  # template stays non-empty
