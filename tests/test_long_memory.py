"""SQLite durable memory tests."""

from __future__ import annotations

from harness.long_memory import (
    DEFAULT_SYSTEM_PROMPT,
    LongMemory,
    default_sqlite_path,
    load_durable_block,
    maybe_migrate_legacy_db,
    normalize_kind,
    resolve_sqlite_path,
)
from harness.loop import build_system_prompt


def test_upsert_list_delete(tmp_path):
    db = tmp_path / "m.db"
    mem = LongMemory(db)
    e = mem.upsert(
        kind="other",
        key="coding",
        content="优先用 uv",
        title="编码习惯",
    )
    assert e.id > 0
    assert e.key == "default"
    assert mem.list(kind="other")[0].content == "优先用 uv"
    assert mem.delete(e.id) is True
    assert mem.list() == []
    mem.close()


def test_one_entry_per_kind_coalesce(tmp_path):
    db = tmp_path / "m.db"
    mem = LongMemory(db)
    # Simulate pre-migration multi-key rows via raw SQL (old UNIQUE(kind,key)).
    mem.close()
    import sqlite3

    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        DROP TABLE IF EXISTS entries;
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(kind, key)
        );
        INSERT INTO entries(kind,key,title,content,created_at,updated_at)
        VALUES ('other','default','发布流程','本地到正式','t1','t1');
        INSERT INTO entries(kind,key,title,content,created_at,updated_at)
        VALUES ('other','cleanup','临时文件清理','测完清临时文件','t2','t2');
        """
    )
    conn.commit()
    conn.close()

    mem = LongMemory(db)
    others = mem.list(kind="other")
    assert len(others) == 1
    assert others[0].key == "default"
    assert "本地到正式" in others[0].content
    assert "测完清临时文件" in others[0].content
    # Second upsert must replace/append the same row, not create another.
    mem.upsert(kind="other", content="额外一条", append=True)
    others2 = mem.list(kind="other")
    assert len(others2) == 1
    assert "额外一条" in others2[0].content
    mem.close()


def test_normalize_kind_labels():
    assert normalize_kind("铁律") == "iron"
    assert normalize_kind("system") == "system"
    assert normalize_kind("人格") == "persona"


def test_legacy_kind_migration(tmp_path):
    db = tmp_path / "m.db"
    mem = LongMemory(db)
    # Insert via SQL to simulate old kinds (bypass normalize).
    mem._conn.execute(
        "INSERT INTO entries(kind,key,title,content,created_at,updated_at) "
        "VALUES ('rules','r','','old rules','t','t')"
    )
    mem._conn.execute(
        "INSERT INTO entries(kind,key,title,content,created_at,updated_at) "
        "VALUES ('background','b','','old bg','t','t')"
    )
    mem._conn.commit()
    n = mem.migrate_legacy_kinds()
    assert n == 2
    kinds = {e.kind for e in mem.list()}
    assert "iron" in kinds
    assert "other" in kinds
    assert "rules" not in kinds
    mem.close()


def test_seed_system_and_prompt_assembly(tmp_path):
    db = tmp_path / "m.db"
    mem = LongMemory(db)
    seeded = mem.seed_defaults(persona_text="说话极简")
    assert seeded["system"] is True
    assert seeded["iron"] is True
    assert seeded["persona"] is True
    block = mem.render_prompt_block(runtime_env_block="## 当前运行环境\n- 系统族: Windows\n")
    assert "系统提示词" in block
    assert "铁律" in block
    assert "当前运行环境" in block
    assert "说话极简" in block
    assert "你是 Sun" in block
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
    mem.upsert(kind="other", key="me", content="用户在上海做仓储系统", title="背景")
    mem.upsert(kind="persona", key="default", content="说话极简", title="人格")
    mem.close()

    prompt = build_system_prompt(
        persona_path=str(persona),
        sqlite_path=str(db),
    )
    assert "长久记忆" in prompt or "系统提示词" in prompt
    assert "用户在上海做仓储系统" in prompt
    assert "说话极简" in prompt
    assert "你是 Sun" in prompt  # seeded system
    assert "file persona" not in prompt


def test_load_durable_block_empty(tmp_path):
    block, path, has_p = load_durable_block(str(tmp_path / "empty.db"))
    # empty.db gets no seed via load_durable_block (no seed call there)
    assert has_p is False
    assert path.name == "empty.db"
    assert block == "" or "长久记忆" in block


def test_default_sqlite_path_is_project_local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert default_sqlite_path() == tmp_path / "long_memory.db"


def test_migrate_legacy_db_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "legacy_home" / "long_memory.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"sqlite-fake")
    monkeypatch.setattr(
        "harness.long_memory.legacy_sqlite_path",
        lambda: legacy,
    )
    target = tmp_path / "long_memory.db"
    assert maybe_migrate_legacy_db(target) is True
    assert target.read_bytes() == b"sqlite-fake"
    assert maybe_migrate_legacy_db(target) is False


def test_resolve_relative_sqlite_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = resolve_sqlite_path("mem/custom.db")
    assert path == tmp_path / "mem" / "custom.db"


def test_default_system_prompt_nonempty():
    assert "你是 Sun" in DEFAULT_SYSTEM_PROMPT


def test_format_dump_layout(tmp_path):
    db = tmp_path / "m.db"
    mem = LongMemory(db)
    mem.upsert(kind="system", key="default", content="SYS", title="s")
    mem.upsert(kind="persona", key="default", content="PERSONA", title="p")
    text = mem.format_dump()
    assert text.index("【系统提示词】") < text.index("[开发环境]")
    assert text.index("[开发环境]") < text.index("【铁律】")
    assert text.index("【铁律】") < text.index("【人格】")
    assert text.index("【人格】") < text.index("【项目背景】")
    assert "SYS" in text
    assert "PERSONA" in text
    assert "（暂无）" in text  # empty sections
    mem.close()
