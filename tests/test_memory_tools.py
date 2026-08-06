"""memory_* function-calling tools."""

from __future__ import annotations

import json

from harness.config import Settings
from harness.tools import builtins, registry
from harness.tools.builtins import set_sqlite_path
from harness.tools.executor import ToolExecutor


def test_memory_tools_registered():
    for name in ("memory_list", "memory_get", "memory_upsert", "memory_delete"):
        assert registry.get(name) is not None


def test_memory_crud_tools(tmp_path):
    db = tmp_path / "long_memory.db"
    set_sqlite_path(str(db))
    settings = Settings(api_key="x", require_confirmation=False, sqlite_path=str(db))
    ex = ToolExecutor(registry, settings)

    up = ex.execute(
        "memory_upsert",
        json.dumps(
            {
                "kind": "人格",
                "key": "default",
                "title": "测试",
                "content": "极简说话",
            },
            ensure_ascii=False,
        ),
    )
    assert up["success"] is True, up
    assert up["entry"]["kind"] == "persona"

    listed = ex.execute("memory_list", "{}")
    assert listed["success"] is True
    assert listed["count"] >= 1
    assert "【人格】" in listed["formatted"]
    assert "极简说话" in listed["formatted"]
    assert "[开发环境]" in listed["formatted"]
    assert "【系统提示词】" in listed["formatted"]
    assert "【项目背景】" in listed["formatted"]

    got = ex.execute(
        "memory_get",
        json.dumps({"kind": "persona", "key": "default"}),
    )
    assert got["success"] is True
    assert "极简说话" in got["entry"]["content"]

    eid = up["entry"]["id"]
    deleted = ex.execute("memory_delete", json.dumps({"entry_id": eid}))
    assert deleted["success"] is True

    # cleanup holder
    set_sqlite_path("")
    assert builtins._SQLITE_PATH[0] == ""
