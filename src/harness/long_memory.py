"""SQLite long-term memory — durable system prompt / iron rules / persona / etc.

Unlike Redis chat sessions (clear anytime via prune), this store holds content
that must not be casually wiped. Deletes always go through an explicit API
(CLI confirm or dangerous tool confirm).
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import global_config_path

# Canonical kinds (order = prompt assembly order, except runtime env inject).
KINDS = ("system", "iron", "dev_env", "persona", "project", "other")
_KIND_LABELS = {
    "system": "系统提示词",
    "iron": "铁律",
    "dev_env": "开发环境",
    "persona": "人格",
    "project": "项目背景",
    "other": "其他",
}
# User-facing dump order / heading style (matches REPL presentation).
_DISPLAY_SECTIONS: tuple[tuple[str, str], ...] = (
    ("system", "【系统提示词】"),
    ("dev_env", "[开发环境]"),
    ("iron", "【铁律】"),
    ("persona", "【人格】"),
    ("project", "【项目背景】"),
    ("other", "【其他】"),
)
# Old kind → new kind (one-time SQL migrate).
_LEGACY_KIND_MAP = {
    "rules": "iron",
    "background": "other",
    "prompt": "other",
}
_MAX_BLOCK_CHARS = 24_000
_LOCAL_DB_NAME = "long_memory.db"
# Exactly one row per kind; key is kept for display/compat and always this value.
_SINGLE_KEY = "default"

DEFAULT_SYSTEM_PROMPT = """你是 Sun，本机自主编程助手。默认中文（用户另要求除外）。语气跟人格，冲突听铁律。
聊天在 Redis（可 /sessions prune）；长久记忆在 SQLite（不 prune）。
小步：观察 → 工具 → 按真实输出决策；禁止编造命令/工具结果。

环境：按任务注入的「当前运行环境」选命令与路径，勿默认 Linux。
Windows：run_shell 多为 cmd；禁交互 date/time（会挂起）；查时用 python -c 或 powershell Get-Date；勿假设 bash/grep/head（可用 Python）。
Linux/macOS：可用常见 Unix 命令；仍优先专用工具。

编码：先 list_dir / find_files / search_files；改已有用 edit_file（先 read，old_text 唯一；失败重读再试，禁盲覆盖）；新建/整文件重写才 write_file；大文件用 offset/limit；限 workspace；坏了可 git_rollback，用户要求再 git_commit。

验证：改完 check_syntax；有测则 run_tests 直至绿（可选 run_lint）；测红禁 finish；只凭工具输出宣称成功。

规划：不清先 ask_user；非琐碎先 propose_plan（含步骤与验收），获批再大改；todo 最多一个 in_progress；简单问答可跳过 plan。
密钥库：登录、打开站点、账号密码、项目访问地址等凭据需求时，先 secret_vault_search → secret_vault_get，禁止先问用户要地址/账号；token 从配置读取，勿读 .env 原文、勿写入记忆/日志/仓库。

记忆：六类 system / iron / dev_env / persona / project / other；每类仅一条（禁止 other/cleanup 这类多 key）。
自然语言「看/改/加/删记忆」直接调 memory_list|get|upsert|delete，勿让用户敲 /memory。
增补某类时先 memory_get 读出现有全文，再整段 memory_upsert 写回（可拼在原文后）。
看全部：原样输出 formatted（【系统提示词】/[开发环境]/【铁律】/【人格】/【项目背景】/【其他】），禁改成表格或摘要。改 system/iron 需谨慎。

其它：问模型立刻 list_models（禁扫 env / 读 .env）；无专用工具再用 run_shell。
网页：search_web 一次 → 最佳结果 fetch_url 一次；历史已够则禁再抓；信 15 分钟缓存；禁连猜路径；fetch_url 不跑 JS。
REPL 勿让用户重复已知事实；优先一次性 python -c，少落盘临时脚本。
本任务临时文件（_tmp_* / tmp_* / 调试草稿）须在 finish 前删除并等确认；勿删用户原有文件。完成且验证通过后 finish；不可能/不安全则 finish 说明原因。信任「先前对话摘要」，细节再 read/搜索。
退出：用户说退出/再见/quit/bye 时调用 exit_repl（真正结束进程）。finish 只结束本轮任务并回到 sun>，禁止只口头说「已退出」却不调 exit_repl。
记忆推断：人格/其它记忆已写明城市、时区、工作地时，查天气、本地生活类问题直接沿用，勿再反问「你在哪」；用户另说城市再改用。
历史聊天：用户问「之前说过 / 你还记得 / 什么秘密 / 上回聊了什么」等时，先 session_search 查 Redis 会话 transcript；勿只查 memory_list（SQLite 仅含主动写入的长久记忆）。无 Redis 时说明无法跨会话回忆。
"""

DEFAULT_IRON_RULES = """- 铁律 > 系统提示词 > 开发环境/人格/项目/其他。
- 禁编造未执行的命令、测试或工具结果；测红禁 finish。
- 删文件/目录必须确认；禁绕过确认。
- 未经允许禁改任何数据或代码。
- 禁泄敏感信息；授权读密钥库仅临时用，禁滥用/转存。
"""


class LongMemoryError(RuntimeError):
    pass


def legacy_sqlite_path() -> Path:
    """Pre-migration location (~/.config/sun/long_memory.db)."""
    return global_config_path().parent / _LOCAL_DB_NAME


def default_sqlite_path() -> Path:
    """Project-local DB next to `.env` (cwd/long_memory.db); gitignored."""
    return Path.cwd() / _LOCAL_DB_NAME


def maybe_migrate_legacy_db(target: Path) -> bool:
    """Copy ~/.config/sun/long_memory.db → project DB once if target missing."""
    if target.exists():
        return False
    legacy = legacy_sqlite_path()
    if not legacy.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    return True


def resolve_sqlite_path(explicit: str = "") -> Path:
    if explicit.strip():
        path = Path(explicit.strip()).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
    path = default_sqlite_path()
    maybe_migrate_legacy_db(path)
    return path


def normalize_kind(kind: str) -> str:
    """Accept English keys or Chinese labels; return canonical kind."""
    raw = (kind or "").strip()
    if not raw:
        raise LongMemoryError(f"kind required; use one of {KINDS}")
    if raw in KINDS:
        return raw
    for k, label in _KIND_LABELS.items():
        if raw == label:
            return k
    # aliases
    aliases = {
        "rules": "iron",
        "hard": "iron",
        "铁律": "iron",
        "background": "other",
        "prompt": "other",
        "系统提示": "system",
        "系统提示词": "system",
        "开发环境": "dev_env",
        "人格": "persona",
        "项目背景": "project",
        "projects": "project",
        "其他": "other",
    }
    if raw in aliases:
        return aliases[raw]
    raise LongMemoryError(f"Unknown kind {kind!r}; use one of {KINDS} or {_KIND_LABELS}")


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
            "kind_label": _KIND_LABELS.get(self.kind, self.kind),
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
        self.migrate_legacy_kinds()
        self.coalesce_one_per_kind()
        self._ensure_unique_kind_schema()

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
                UNIQUE(kind)
            )
            """
        )
        # Older DBs may still have UNIQUE(kind,key); coalesce + rebuild fixes that.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind)"
        )
        self._conn.commit()

    def migrate_legacy_kinds(self) -> int:
        """Rename old kinds (rules/background/prompt) to the new taxonomy."""
        changed = 0
        for old, new in _LEGACY_KIND_MAP.items():
            cur = self._conn.execute("SELECT * FROM entries WHERE kind=?", (old,))
            for row in cur.fetchall():
                eid = int(row["id"])
                existing = self._conn.execute(
                    "SELECT id, content, title FROM entries WHERE kind=? AND id!=?",
                    (new, eid),
                ).fetchone()
                if existing is None:
                    key = str(row["key"] or _SINGLE_KEY)
                    clash = self._conn.execute(
                        "SELECT 1 FROM entries WHERE kind=? AND key=?", (new, key)
                    ).fetchone()
                    new_key = f"{key}_from_{old}" if clash else key
                    try:
                        self._conn.execute(
                            "UPDATE entries SET kind=?, key=? WHERE id=?",
                            (new, new_key, eid),
                        )
                    except sqlite3.IntegrityError:
                        # UNIQUE(kind): fold into the sole target row.
                        existing = self._conn.execute(
                            "SELECT id, content, title FROM entries WHERE kind=?",
                            (new,),
                        ).fetchone()
                        if existing is None:
                            raise
                        self._merge_row_into(int(existing["id"]), row)
                        self._conn.execute("DELETE FROM entries WHERE id=?", (eid,))
                else:
                    self._merge_row_into(int(existing["id"]), row)
                    self._conn.execute("DELETE FROM entries WHERE id=?", (eid,))
                changed += 1
        if changed:
            self._conn.commit()
        return changed

    def _merge_row_into(self, keep_id: int, donor: sqlite3.Row) -> None:
        keep = self._conn.execute(
            "SELECT content, title FROM entries WHERE id=?", (keep_id,)
        ).fetchone()
        if keep is None:
            return
        a = str(keep["content"] or "").strip()
        b = str(donor["content"] or "").strip()
        title_d = str(donor["title"] or "").strip()
        key_d = str(donor["key"] or "").strip()
        if title_d and key_d not in {"", _SINGLE_KEY}:
            chunk = f"## {title_d}\n{b}" if b else f"## {title_d}"
        else:
            chunk = b
        merged = "\n\n".join(p for p in (a, chunk) if p).strip() or "(empty)"
        self._conn.execute(
            "UPDATE entries SET content=?, key=?, updated_at=? WHERE id=?",
            (merged, _SINGLE_KEY, _utcnow(), keep_id),
        )

    def coalesce_one_per_kind(self) -> int:
        """Merge multiple rows of the same kind into one (_SINGLE_KEY). Returns kinds merged."""
        cur = self._conn.execute(
            "SELECT kind, COUNT(*) AS n FROM entries GROUP BY kind HAVING n > 1"
        )
        dup_kinds = [str(r["kind"]) for r in cur.fetchall()]
        if not dup_kinds:
            # Still normalize keys to default when single row has non-default key.
            self._conn.execute(
                "UPDATE entries SET key=? WHERE key!=?",
                (_SINGLE_KEY, _SINGLE_KEY),
            )
            self._conn.commit()
            return 0

        merged = 0
        for kind in dup_kinds:
            rows = self._conn.execute(
                "SELECT * FROM entries WHERE kind=? ORDER BY id", (kind,)
            ).fetchall()
            if len(rows) < 2:
                continue
            parts: list[str] = []
            titles: list[str] = []
            for r in rows:
                body = str(r["content"] or "").strip()
                title = str(r["title"] or "").strip()
                key = str(r["key"] or "").strip()
                if title and title != _KIND_LABELS.get(kind, kind) and key != _SINGLE_KEY:
                    parts.append(f"## {title}\n{body}" if body else f"## {title}")
                elif body:
                    parts.append(body)
                if title:
                    titles.append(title)
            content = "\n\n".join(p for p in parts if p).strip()
            if not content:
                content = "(empty)"
            keep_id = int(rows[0]["id"])
            created = str(rows[0]["created_at"] or _utcnow())
            updated = str(rows[-1]["updated_at"] or _utcnow())
            title = (
                _KIND_LABELS.get(kind, kind)
                if len(set(titles)) != 1
                else titles[0]
            )
            self._conn.execute(
                """
                UPDATE entries
                SET key=?, title=?, content=?, created_at=?, updated_at=?
                WHERE id=?
                """,
                (_SINGLE_KEY, title, content, created, updated, keep_id),
            )
            for r in rows[1:]:
                self._conn.execute("DELETE FROM entries WHERE id=?", (int(r["id"]),))
            merged += 1
        self._conn.commit()
        return merged

    def _schema_has_unique_kind(self) -> bool:
        """True when table enforces UNIQUE on kind alone."""
        cur = self._conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'")
        row = cur.fetchone()
        if not row or not row["sql"]:
            return False
        sql = str(row["sql"]).upper().replace(" ", "")
        # UNIQUE(kind) or kind TEXT NOT NULL UNIQUE
        return "UNIQUE(KIND)" in sql or "KINDTEXTNOTNULLUNIQUE" in sql

    def _ensure_unique_kind_schema(self) -> None:
        """Rebuild table so kind is UNIQUE (one human-facing section per kind)."""
        if self._schema_has_unique_kind():
            return
        self.coalesce_one_per_kind()
        self._conn.execute(
            """
            CREATE TABLE entries_onekind (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL UNIQUE,
                key TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            INSERT INTO entries_onekind(id, kind, key, title, content, created_at, updated_at)
            SELECT id, kind, ?, title, content, created_at, updated_at FROM entries
            """,
            (_SINGLE_KEY,),
        )
        self._conn.execute("DROP TABLE entries")
        self._conn.execute("ALTER TABLE entries_onekind RENAME TO entries")
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
            kind = normalize_kind(kind)
            cur = self._conn.execute(
                "SELECT * FROM entries WHERE kind=? ORDER BY kind, key", (kind,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM entries ORDER BY kind, key")
        return [self._row(r) for r in cur.fetchall()]

    def get(self, entry_id: int) -> MemoryEntry | None:
        cur = self._conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,))
        row = cur.fetchone()
        return self._row(row) if row else None

    def get_by_key(self, kind: str, key: str = "") -> MemoryEntry | None:
        """Return the single entry for kind (key ignored; always one-per-kind)."""
        kind = normalize_kind(kind)
        cur = self._conn.execute(
            "SELECT * FROM entries WHERE kind=? LIMIT 1", (kind,)
        )
        row = cur.fetchone()
        return self._row(row) if row else None

    def get_kind(self, kind: str) -> MemoryEntry | None:
        return self.get_by_key(kind)

    def upsert(
        self,
        *,
        kind: str,
        key: str = "",
        content: str,
        title: str = "",
        append: bool = False,
    ) -> MemoryEntry:
        """Create or replace the sole entry for ``kind`` (one human section each)."""
        kind = normalize_kind(kind)
        content = (content or "").strip()
        if not content:
            raise LongMemoryError("content must be non-empty")
        now = _utcnow()
        existing = self.get_kind(kind)
        if existing is not None and append:
            content = (existing.content.strip() + "\n\n" + content).strip()
        title_f = (title or "").strip() or (
            existing.title if existing and existing.title else _KIND_LABELS.get(kind, kind)
        )
        if existing is not None:
            self._conn.execute(
                """
                UPDATE entries
                SET key=?, title=?, content=?, updated_at=?
                WHERE id=?
                """,
                (_SINGLE_KEY, title_f, content, now, existing.id),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO entries(kind, key, title, content, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kind, _SINGLE_KEY, title_f, content, now, now),
            )
        self._conn.commit()
        entry = self.get_kind(kind)
        assert entry is not None
        return entry

    def delete(self, entry_id: int) -> bool:
        """Delete one entry by id. Caller must confirm — never auto-prune."""
        cur = self._conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def has_kind(self, kind: str) -> bool:
        kind = normalize_kind(kind)
        cur = self._conn.execute(
            "SELECT 1 FROM entries WHERE kind=? LIMIT 1", (kind,)
        )
        return cur.fetchone() is not None

    def seed_system_if_empty(
        self,
        content: str = DEFAULT_SYSTEM_PROMPT,
        *,
        title: str = "系统提示词",
    ) -> bool:
        if self.has_kind("system"):
            return False
        text = (content or "").strip()
        if not text:
            return False
        self.upsert(kind="system", key="default", content=text, title=title)
        return True

    def seed_iron_if_empty(
        self,
        content: str = DEFAULT_IRON_RULES,
        *,
        title: str = "铁律",
    ) -> bool:
        if self.has_kind("iron"):
            return False
        text = (content or "").strip()
        if not text:
            return False
        self.upsert(kind="iron", key="default", content=text, title=title)
        return True

    def seed_persona_if_empty(self, content: str, *, title: str = "默认人格") -> bool:
        """Insert persona/default once when no persona rows exist."""
        if self.has_kind("persona"):
            return False
        text = (content or "").strip()
        if not text:
            return False
        self.upsert(kind="persona", key="default", content=text, title=title)
        return True

    def seed_defaults(
        self,
        *,
        persona_text: str = "",
        system_text: str = DEFAULT_SYSTEM_PROMPT,
        iron_text: str = DEFAULT_IRON_RULES,
    ) -> dict[str, bool]:
        """Seed missing core kinds. Returns which kinds were inserted."""
        return {
            "system": self.seed_system_if_empty(system_text),
            "iron": self.seed_iron_if_empty(iron_text),
            "persona": self.seed_persona_if_empty(persona_text) if persona_text else False,
        }

    def format_dump(self, kind: str | None = None) -> str:
        """Human-readable dump of all (or one) kinds in the fixed section layout."""
        if kind:
            kind = normalize_kind(kind)
            sections = tuple(
                (k, h) for k, h in _DISPLAY_SECTIONS if k == kind
            )
            if not sections:
                sections = ((kind, f"【{_KIND_LABELS.get(kind, kind)}】"),)
        else:
            sections = _DISPLAY_SECTIONS

        by_kind: dict[str, list[MemoryEntry]] = {k: [] for k in KINDS}
        for e in self.list(kind=kind):
            by_kind.setdefault(e.kind, []).append(e)

        blocks: list[str] = []
        for k, heading in sections:
            group = by_kind.get(k) or []
            if not group:
                body = "（暂无）"
            elif len(group) == 1:
                body = group[0].content.strip()
            else:
                parts: list[str] = []
                for e in group:
                    label = e.title or e.key
                    parts.append(f"—— {label} [{e.kind}/{e.key}]\n{e.content.strip()}")
                body = "\n\n".join(parts)
            blocks.append(f"{heading}\n{body}")
        return "\n\n".join(blocks)

    def render_prompt_block(self, *, runtime_env_block: str = "") -> str:
        """Assemble durable memory (+ optional live runtime env) for the LLM."""
        entries = self.list()
        by_kind: dict[str, list[MemoryEntry]] = {k: [] for k in KINDS}
        for e in entries:
            by_kind.setdefault(e.kind, []).append(e)

        lines: list[str] = []
        total = 0

        def _append_group(kind: str, *, heading: str | None = None) -> None:
            nonlocal total
            group = by_kind.get(kind) or []
            if not group:
                return
            label = heading or f"{_KIND_LABELS.get(kind, kind)}（{kind}）"
            chunk_h = f"\n## {label}\n"
            if total + len(chunk_h) > _MAX_BLOCK_CHARS:
                lines.append("\n… [长久记忆截断，部分条目未注入]\n")
                return
            lines.append(chunk_h)
            total += len(chunk_h)
            # One entry per kind — inject body under the kind heading only.
            e = group[0]
            chunk = f"{e.content}\n"
            if total + len(chunk) > _MAX_BLOCK_CHARS:
                lines.append("\n… [长久记忆截断，部分条目未注入]\n")
                return
            lines.append(chunk)
            total += len(chunk)

        _append_group("system")
        _append_group("iron")
        if runtime_env_block.strip():
            block = "\n" + runtime_env_block.strip() + "\n"
            if total + len(block) <= _MAX_BLOCK_CHARS:
                lines.append(block)
                total += len(block)
        _append_group("dev_env")
        _append_group("persona")
        _append_group("project")
        _append_group("other")

        if not lines:
            return ""
        preface = (
            "\n## 长久记忆（SQLite）\n"
            "分类：系统提示词 / 铁律 / 开发环境 / 人格 / 项目背景 / 其他。"
            "冲突时以铁律为准。聊天在 Redis，本库不被 prune 清除。\n"
        )
        return preface + "".join(lines)


def open_long_memory(explicit: str = "") -> LongMemory:
    return LongMemory(resolve_sqlite_path(explicit))


def load_durable_block(
    explicit: str = "",
    *,
    runtime_env_block: str = "",
) -> tuple[str, Path, bool]:
    """Return (prompt_block, db_path, has_persona_rows)."""
    path = resolve_sqlite_path(explicit)
    mem = LongMemory(path)
    try:
        has_persona = mem.has_kind("persona")
        return (
            mem.render_prompt_block(runtime_env_block=runtime_env_block),
            path,
            has_persona,
        )
    finally:
        mem.close()
