"""SQLite long-term memory — durable identity / rules / background.

Unlike Redis chat sessions (clear anytime via prune), this store is for
prompts, persona, rules, and background that must not be casually wiped.
Deletes always go through an explicit API (CLI confirms).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import global_config_path

KINDS = ("persona", "rules", "background", "prompt")
_KIND_LABELS = {
    "persona": "人格",
    "rules": "规则",
    "background": "背景",
    "prompt": "提示词补充",
}
_MAX_BLOCK_CHARS = 24_000


class LongMemoryError(RuntimeError):
    pass


def default_sqlite_path() -> Path:
    return global_config_path().parent / "long_memory.db"


def resolve_sqlite_path(explicit: str = "") -> Path:
    if explicit.strip():
        return Path(explicit.strip()).expanduser()
    return default_sqlite_path()


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class MemoryEntry:
    id: int
    kind: str
    key: str
    title: str
    content: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class LongMemory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(kind, key)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind)"
        )
        self._conn.commit()

    def _row(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=int(row["id"]),
            kind=str(row["kind"]),
            key=str(row["key"]),
            title=str(row["title"] or ""),
            content=str(row["content"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def list(self, kind: str | None = None) -> list[MemoryEntry]:
        if kind:
            if kind not in KINDS:
                raise LongMemoryError(f"Unknown kind {kind!r}; use one of {KINDS}")
            cur = self._conn.execute(
                "SELECT * FROM entries WHERE kind=? ORDER BY kind, key", (kind,)
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM entries ORDER BY kind, key"
            )
        return [self._row(r) for r in cur.fetchall()]

    def get(self, entry_id: int) -> MemoryEntry | None:
        cur = self._conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,))
        row = cur.fetchone()
        return self._row(row) if row else None

    def get_by_key(self, kind: str, key: str) -> MemoryEntry | None:
        cur = self._conn.execute(
            "SELECT * FROM entries WHERE kind=? AND key=?", (kind, key)
        )
        row = cur.fetchone()
        return self._row(row) if row else None

    def upsert(
        self,
        *,
        kind: str,
        key: str,
        content: str,
        title: str = "",
    ) -> MemoryEntry:
        if kind not in KINDS:
            raise LongMemoryError(f"Unknown kind {kind!r}; use one of {KINDS}")
        key = (key or "default").strip()
        if not key:
            raise LongMemoryError("key must be non-empty")
        content = (content or "").strip()
        if not content:
            raise LongMemoryError("content must be non-empty")
        now = _utcnow()
        self._conn.execute(
            """
            INSERT INTO entries(kind, key, title, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, key) DO UPDATE SET
                title=excluded.title,
                content=excluded.content,
                updated_at=excluded.updated_at
            """,
            (kind, key, title.strip(), content, now, now),
        )
        self._conn.commit()
        entry = self.get_by_key(kind, key)
        assert entry is not None
        return entry

    def delete(self, entry_id: int) -> bool:
        """Delete one entry by id. Caller must confirm — never auto-prune."""
        cur = self._conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def has_kind(self, kind: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM entries WHERE kind=? LIMIT 1", (kind,)
        )
        return cur.fetchone() is not None

    def seed_persona_if_empty(self, content: str, *, title: str = "默认人格") -> bool:
        """Insert persona/default once when no persona rows exist."""
        if self.has_kind("persona"):
            return False
        text = (content or "").strip()
        if not text:
            return False
        self.upsert(kind="persona", key="default", content=text, title=title)
        return True

    def render_prompt_block(self) -> str:
        """Markdown block for system prompt injection."""
        entries = self.list()
        if not entries:
            return ""
        lines = [
            "\n## 长久记忆（SQLite，稳定保留；非聊天记录）\n",
            "下列为人设/规则/背景/提示补充。与硬性安全规则冲突时以硬性规则为准。\n"
            "聊天会话在 Redis，可用 /sessions prune 清除；本库不会被 prune 清掉。\n",
        ]
        by_kind: dict[str, list[MemoryEntry]] = {k: [] for k in KINDS}
        for e in entries:
            by_kind.setdefault(e.kind, []).append(e)

        total = 0
        for kind in KINDS:
            group = by_kind.get(kind) or []
            if not group:
                continue
            label = _KIND_LABELS.get(kind, kind)
            lines.append(f"### {label}（{kind}）\n")
            for e in group:
                head = e.title or e.key
                chunk = f"#### {head} [{e.kind}/{e.key}]\n{e.content}\n"
                if total + len(chunk) > _MAX_BLOCK_CHARS:
                    lines.append("\n… [长久记忆截断，部分条目未注入]\n")
                    return "".join(lines)
                lines.append(chunk)
                total += len(chunk)
        return "".join(lines)


def open_long_memory(explicit: str = "") -> LongMemory:
    return LongMemory(resolve_sqlite_path(explicit))


def load_durable_block(explicit: str = "") -> tuple[str, Path, bool]:
    """Return (prompt_block, db_path, has_persona_rows)."""
    path = resolve_sqlite_path(explicit)
    mem = LongMemory(path)
    try:
        has_persona = mem.has_kind("persona")
        return mem.render_prompt_block(), path, has_persona
    finally:
        mem.close()
