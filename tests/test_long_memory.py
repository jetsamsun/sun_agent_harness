"""SQLite durable memory tests."""

from __future__ import annotations

from harness.long_memory import LongMemory, load_durable_block
from harness.loop import build_system_prompt


def test_upsert_list_delete(tmp_path):
    db = tmp_path / "m.db"
    mem = LongMemory(db)
    e = mem.upsert(
        kind="rules",
        key="coding",
        content="优先用 uv",
        title="编码习惯",
    )
    assert e.id > 0
    assert mem.list(kind="rules")[0].content == "优先用 uv"
    assert mem.delete(e.id) is True
    assert mem.list() == []
    mem.close()


def test_seed_persona_once(tmp_path):
    db = tmp_path / "m.db"
    mem = LongMemory(db)
    assert mem.seed_persona_if_empty("我是测试人格") is True
    assert mem.seed_persona_if_empty("另一份") is False
    assert mem.get_by_key("persona", "default").content == "我是测试人格"
    mem.close()


def test_build_system_prompt_injects_sqlite(tmp_path):
    db = tmp_path / "long.db"
    persona = tmp_path / "PERSONA.md"
    persona.write_text("# file persona\n", encoding="utf-8")
    mem = LongMemory(db)
    mem.upsert(kind="background", key="me", content="用户在上海做仓储系统", title="背景")
    mem.upsert(kind="persona", key="default", content="说话极简", title="人格")
    mem.close()

    prompt = build_system_prompt(
        persona_path=str(persona),
        sqlite_path=str(db),
    )
    assert "长久记忆" in prompt
    assert "用户在上海做仓储系统" in prompt
    assert "说话极简" in prompt
    # SQLite has persona → file PERSONA.md not also injected as fallback block
    assert "file persona" not in prompt


def test_load_durable_block_empty(tmp_path):
    block, path, has_p = load_durable_block(str(tmp_path / "empty.db"))
    assert block == ""
    assert has_p is False
    assert path.name == "empty.db"
