"""The agent loop — the heart of the harness.

Drives the reasoning <-> tool cycle:
    1. Send messages + tool schemas to the model.
    2. If the model returns tool_calls, execute each and feed results back.
    3. Repeat until the model calls `finish`, returns no tool call, or we hit
       the max-turns cap.

Emits structured events through a callback so the CLI (or tests) can render
progress without the loop knowing anything about presentation.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .context import Context
from .llm import LLMClient
from .long_memory import load_durable_block, open_long_memory
from .persona import (
    DEFAULT_PERSONA,
    load_persona_block,
    load_persona_text,
    resolve_persona_path,
)
from .session_store import SessionMeta, SessionStore, SessionStoreError, new_session_id
from .tools.builtins import (
    export_planning_state,
    import_planning_state,
    reset_planning_state,
)
from .tools.executor import ToolExecutor
from .tools.registry import ToolRegistry
from .usage import UsageTotals

# Base rules (Chinese). Runtime OS + editable persona appended via build_system_prompt().
SYSTEM_PROMPT_BASE = """你是 Sun，运行在用户本机上的自主编程助手。
请用中文与用户沟通（除非用户要求其他语言）。
语气与偏好以「长久记忆 / 人格」段为准（SQLite 长久记忆 + 可选 PERSONA.md）；
与下列硬性规则冲突时以硬性规则为准。
聊天记录在 Redis（可 /sessions prune）；人设/规则/背景在 SQLite（不会被 prune 清掉）。

通过调用工具完成任务。按小步推进：观察 → 行动 → 根据工具真实输出再决策。
不要编造命令结果。

## 环境规则（必须遵守）
- 每条任务开始时，系统会注入「当前运行环境」；命令与路径必须按该环境选择，不要默认当成 Linux。
- Windows：`run_shell` 通常走 cmd。禁止使用交互式 `date` / `time`（会挂起直到超时）。
  查日期时间用 `python -c "..."` 或 `powershell -NoProfile -Command "Get-Date ..."`。
  路径可用反斜杠或正斜杠；不要假设有 bash/`grep`/`head` 等 Unix 工具（可用 Python 代替）。
- Linux/macOS：可用常见 Unix 命令；仍优先用专用工具而非巨型 shell 脚本。

## 编码规则
- 改代码前先用 list_dir / find_files / search_files 探索。
- 改已有文件优先 edit_file；新建或整文件重写才用 write_file。
- edit_file 前先 read_file 相关片段，保证 old_text 只匹配一次；失败则重读再试，禁止盲目整文件覆盖。
- 大文件用 read_file 的 offset/limit，不要一次读完全文。
- 写入限制在 workspace 内；编辑可能弹出 diff 确认。任务内首次改文件可能自动 git checkpoint；
  改坏了可用 git_rollback；用户要求保存时再 git_commit。

## 验证规则（不可跳过）
- 改完源码后对文件 check_syntax。
- 有测试则 run_tests；失败则修复再测直到通过。可选 run_lint。
- 上次 run_tests 未通过时不要 finish（系统会拒绝）。
- 只能根据工具输出宣称成功，禁止凭空断言。

## 规划规则（多步/含糊任务）
- 需求不清时先 ask_user。
- 非琐碎任务先 propose_plan（含步骤与验收标准），等 approved=true 再大改。
- 批准后用 todo_write 跟踪；同时最多一个 in_progress。
- 简单问答、单次事实查询可跳过 propose_plan。

## 一般规则
- 没有专用工具时再用 run_shell。
- 读网页 / 抓公开页面内容用 fetch_url（会抽成可读文本），不要 curl 落盘或写临时脚本。
  fetch_url 不执行 JavaScript；强依赖前端渲染的页面可能内容不全。
- 网页规则（省次数）：
  1) 会话历史或先前 tool 结果已够回答时，禁止再抓。
  2) 不确定确切 URL 时：先 search_web 一次，再对最佳结果 fetch_url 一次；
     禁止连猜多个路径变体（如 /guides/foo、/guides/using_foo、/api/foo）。
  3) 同一 URL 进程内约 15 分钟会复用缓存（cached=true）；不要为「再确认」重复 fetch。
- 交互 REPL 中保留历史，不要让用户重复已说过的事实。
- 优先用一次性命令（如 `python -c`）完成本地查询，避免为小事落盘脚本。
- 删除文件/目录一律需要用户确认（系统会对 rm/del/Remove-Item/os.remove 等弹确认）。
  禁止设法绕过确认；用户拒绝则停止删除并说明。
- 若任务中创建了临时文件（如 `_weather.py`、`weather*.txt`、`tmp_*`、仅用于调试的草稿），
  必须在 finish 之前删除它们（Windows: `del` / `Remove-Item`；Unix: `rm`），并等待确认通过。
  不要删除用户原有项目文件、`.git`、配置或测试夹具；只清本任务产生的临时产物。
- 任务完成且验证通过、临时文件已清理后，再调用 finish，摘要简洁。
- 不可能或不安全时用 finish 说明原因，不要猜。
- 长任务中系统可能把较早轮次压成「先前对话摘要」；请信任摘要并继续，
  需要细节时再 read_file / 搜索，不要要求用户重复已说过的目标。
"""


def detect_runtime_env() -> dict[str, str]:
    """Probe OS / shell / cwd for injection into the system prompt."""
    system = platform.system() or "Unknown"
    if system == "Windows":
        family = "Windows"
        shell_hint = "cmd（subprocess shell=True；非 bash）"
        date_hint = (
            "禁止交互式 date/time；用 python -c 或 powershell Get-Date 查时间"
        )
    elif system == "Darwin":
        family = "macOS"
        shell_hint = "sh/bash 系"
        date_hint = "可用 date；也可用 python -c"
    else:
        family = "Linux" if system == "Linux" else system
        shell_hint = "sh/bash 系"
        date_hint = "可用 date；也可用 python -c"

    return {
        "family": family,
        "system": system,
        "release": platform.release() or "",
        "machine": platform.machine() or "",
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "shell_hint": shell_hint,
        "date_hint": date_hint,
        "path_sep": os.sep,
    }


def build_system_prompt(
    env: dict[str, str] | None = None,
    *,
    persona_path: str = "",
    sqlite_path: str = "",
) -> str:
    """SYSTEM_PROMPT_BASE + env + SQLite durable memory (+ PERSONA.md fallback)."""
    info = env or detect_runtime_env()
    env_block = f"""
## 当前运行环境（本任务启动时检测，请严格按此选择命令）
- 系统族: {info["family"]}（platform.system={info["system"]}）
- 版本/架构: {info["release"]} / {info["machine"]}
- Python: {info["python"]}
- 工作目录 cwd: {info["cwd"]}
- 路径分隔符: {info["path_sep"]!r}
- Shell: {info["shell_hint"]}
- 日期时间: {info["date_hint"]}
"""
    # Seed SQLite persona once from PERSONA.md / default template.
    try:
        mem = open_long_memory(sqlite_path)
        try:
            if not mem.has_kind("persona"):
                file_text = load_persona_text(resolve_persona_path(persona_path))
                mem.seed_persona_if_empty(file_text or DEFAULT_PERSONA)
        finally:
            mem.close()
    except OSError:
        pass

    durable_block, _db, has_persona = load_durable_block(sqlite_path)
    # File PERSONA.md only if SQLite has no persona rows (compat / override path).
    persona_block = ""
    if not has_persona:
        persona_block, _ = load_persona_block(persona_path)
    return (
        SYSTEM_PROMPT_BASE.rstrip()
        + "\n"
        + env_block
        + durable_block
        + persona_block
    )


@dataclass
class Event:
    kind: str
    # think | think_delta | tool_call | tool_result | finish | stop | usage | turn
    data: dict[str, Any]


EventFn = Callable[[Event], None]


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        executor: ToolExecutor,
        settings: Settings,
        on_event: EventFn | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._executor = executor
        self._settings = settings
        self._emit = on_event or (lambda e: None)
        self._store = store
        # REPL session memory. Cleared via clear_session() / /new.
        self._session: Context | None = None
        self._session_usage = UsageTotals(model=settings.model)
        self._session_id: str | None = None
        self._session_cwd: str | None = None
        self._session_title: str = ""
        self._session_created_at: str | None = None
        # Full append-only log (never compressed); Redis transcript source.
        self._transcript: list[dict[str, Any]] = []

    def clear_session(self) -> None:
        """Drop REPL history and planning state (todos / plan approvals)."""
        self._session = None
        self._session_id = None
        self._session_cwd = None
        self._session_title = ""
        self._session_created_at = None
        self._transcript = []
        reset_planning_state()
        self._session_usage = UsageTotals(model=self._settings.model)

    def session_context(self) -> Context | None:
        return self._session

    def session_usage(self) -> UsageTotals:
        return self._session_usage

    def session_id(self) -> str | None:
        return self._session_id

    def has_store(self) -> bool:
        return self._store is not None

    def require_store(self) -> SessionStore:
        if self._store is None:
            raise SessionStoreError(
                "Session persistence requires SUN_REDIS_URL in .env "
                "(redis://host:6379/0). Configure Redis, then retry."
            )
        return self._store

    def list_sessions(self, *, limit: int = 50) -> list[SessionMeta]:
        store = self.require_store()
        return store.list_sessions(cwd=str(Path.cwd()), limit=limit)

    def prune_sessions(self) -> list[str]:
        store = self.require_store()
        return store.prune(cwd=str(Path.cwd()))

    def resume_session(self, session_id: str) -> str:
        """Load a persisted session for the current cwd into this REPL.

        Restores live Context messages (working state), not the full transcript
        into the model window. Transcript stays in Redis for audit/deep-resume.
        """
        store = self.require_store()
        sid = session_id.strip()
        meta = store.get_meta(sid)
        if meta is None:
            raise SessionStoreError(f"Unknown session: {sid}")
        cwd_now = str(Path.cwd().resolve())
        if meta.cwd != cwd_now:
            raise SessionStoreError(
                f"Session {sid} is bound to cwd:\n  {meta.cwd}\n"
                f"Current cwd:\n  {cwd_now}\n"
                "cd to that directory, then /resume again."
            )
        state = store.get_state(sid)
        if not state or not isinstance(state.get("messages"), list):
            raise SessionStoreError(f"Session {sid} has no usable state")

        env = detect_runtime_env()
        system_prompt = build_system_prompt(
            env,
            persona_path=self._settings.persona_path,
            sqlite_path=self._settings.sqlite_path,
        )
        ctx = Context.from_messages(state["messages"])
        ctx.set_system_prompt(system_prompt)
        import_planning_state(
            {"todos": state.get("todos") or [], "plan": state.get("plan")}
        )

        self._session = ctx
        self._session_id = sid
        self._session_cwd = meta.cwd
        self._session_title = meta.title
        self._session_created_at = meta.created_at
        self._transcript = store.get_transcript(sid)
        self._session_usage = UsageTotals(model=self._settings.model)
        self._emit(Event("session", {"action": "resume", "id": sid, "title": meta.title}))
        return sid

    def run(self, task: str, *, session: bool = False) -> str:
        """Run one user task.

        session=False (default): fresh Context — used by `sun "task"`.
        session=True: reuse in-memory Context across calls — used by REPL.
        """
        env = detect_runtime_env()
        system_prompt = build_system_prompt(
            env,
            persona_path=self._settings.persona_path,
            sqlite_path=self._settings.sqlite_path,
        )
        self._emit(Event("env", {"env": env}))

        if session:
            if self._session is None:
                reset_planning_state()
                self._session = Context(system_prompt)
                self._transcript = []
                if self._store is not None:
                    self._session_id = new_session_id()
                    self._session_cwd = str(Path.cwd().resolve())
                    self._session_created_at = None
                    self._session_title = task.strip().replace("\n", " ")[:80]
                    self._emit(
                        Event("session", {"action": "new", "id": self._session_id})
                    )
            else:
                # Reload PERSONA.md / env block every user turn without dropping history.
                self._session.set_system_prompt(system_prompt)
            ctx = self._session
            usage = self._session_usage
        else:
            reset_planning_state()
            ctx = Context(system_prompt)
            usage = UsageTotals(model=self._settings.model)

        ctx.add_user(task)
        if session:
            self._transcript.append({"role": "user", "content": task})
        tools = self._registry.openai_schemas()
        # None = tests never run this user line; False = last run_tests failed.
        tests_passed: bool | None = None
        run_started = time.perf_counter()
        finished = False
        final_text = ""

        try:
            for turn in range(1, self._settings.max_turns + 1):
                self._maybe_compress_context(ctx, turn)

                streamed_bits: list[str] = []

                def on_delta(
                    text: str,
                    _turn: int = turn,
                    _bits: list[str] = streamed_bits,
                ) -> None:
                    _bits.append(text)
                    self._emit(Event("think_delta", {"text": text, "turn": _turn}))

                use_stream = bool(self._settings.streaming)
                chat = self._llm.chat(
                    ctx.messages(),
                    tools=tools,
                    on_delta=on_delta if use_stream else None,
                )
                message = chat.message
                usage.add_llm(
                    turn=turn,
                    prompt_tokens=chat.prompt_tokens,
                    completion_tokens=chat.completion_tokens,
                    latency_ms=chat.latency_ms,
                    streamed=chat.streamed,
                    cache_hit_tokens=chat.cache_hit_tokens,
                    cache_miss_tokens=chat.cache_miss_tokens,
                )
                self._emit(
                    Event(
                        "turn",
                        {
                            "turn": turn,
                            "prompt_tokens": chat.prompt_tokens,
                            "completion_tokens": chat.completion_tokens,
                            "cache_hit_tokens": chat.cache_hit_tokens,
                            "cache_miss_tokens": chat.cache_miss_tokens,
                            "latency_ms": round(chat.latency_ms, 1),
                            "streamed": chat.streamed,
                        },
                    )
                )
                ctx.add_assistant(message)
                if session:
                    self._append_transcript_assistant(message)

                tool_calls = getattr(message, "tool_calls", None)

                # No tool call: the model is talking to us. Treat text as final.
                if not tool_calls:
                    text = message.content or ""
                    if chat.streamed and streamed_bits:
                        self._emit(
                            Event(
                                "think",
                                {
                                    "text": "".join(streamed_bits),
                                    "turn": turn,
                                    "streamed": True,
                                },
                            )
                        )
                    self._emit_finish(text, turn, usage, run_started)
                    finished = True
                    final_text = text
                    return text

                if message.content:
                    self._emit(
                        Event(
                            "think",
                            {
                                "text": message.content,
                                "turn": turn,
                                "streamed": chat.streamed,
                            },
                        )
                    )

                for tc in tool_calls:
                    name = tc.function.name
                    raw_args = tc.function.arguments
                    self._emit(
                        Event("tool_call", {"name": name, "args": raw_args, "turn": turn})
                    )

                    t0 = time.perf_counter()
                    result = self._executor.execute(name, raw_args)
                    tool_ms = (time.perf_counter() - t0) * 1000
                    usage.add_tool(latency_ms=tool_ms)

                    if name == "run_tests":
                        tests_passed = bool(result.get("passed"))

                    if name == "finish" and result.get("finished") and tests_passed is False:
                        result = {
                            "success": False,
                            "finished": False,
                            "error": (
                                "Refused finish: last run_tests failed. "
                                "Fix the failures, call run_tests until passed=true, then finish."
                            ),
                        }

                    self._emit(
                        Event(
                            "tool_result",
                            {
                                "name": name,
                                "result": result,
                                "latency_ms": round(tool_ms, 1),
                                "turn": turn,
                            },
                        )
                    )

                    tool_payload = json.dumps(result, ensure_ascii=False)
                    ctx.add_tool_result(tc.id, tool_payload)
                    if session:
                        self._transcript.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": tool_payload,
                            }
                        )

                    if result.get("finished"):
                        summary = result.get("summary", "")
                        self._emit_finish(summary, turn, usage, run_started)
                        finished = True
                        final_text = summary
                        return summary

            self._emit(
                Event(
                    "stop",
                    {
                        "reason": "max_turns",
                        "limit": self._settings.max_turns,
                        "usage": usage.as_dict(),
                    },
                )
            )
            self._emit(Event("usage", usage.as_dict()))
            final_text = (
                f"[stopped] reached max turns ({self._settings.max_turns}) without finishing."
            )
            return final_text
        finally:
            if session and self._store is not None and self._session_id:
                self._persist_session(status="done" if finished else "active")

    def _append_transcript_assistant(self, message: Any) -> None:
        entry: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if getattr(message, "tool_calls", None):
            entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        self._transcript.append(entry)

    def _persist_session(self, *, status: str) -> None:
        assert self._store is not None and self._session_id and self._session is not None
        planning = export_planning_state()
        title = self._session_title or self._session_id
        try:
            meta = self._store.save(
                session_id=self._session_id,
                cwd=self._session_cwd or str(Path.cwd()),
                title=title,
                model=self._settings.model,
                user_turns=self._session.user_turns(),
                status=status,
                messages=self._session.messages(),
                todos=planning.get("todos") or [],
                plan=planning.get("plan"),
                transcript=self._transcript,
                created_at=self._session_created_at,
            )
            self._session_created_at = meta.created_at
            self._emit(
                Event(
                    "session",
                    {
                        "action": "save",
                        "id": meta.id,
                        "status": status,
                        "user_turns": meta.user_turns,
                    },
                )
            )
        except SessionStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SessionStoreError(f"Failed to persist session: {exc}") from exc

    def _maybe_compress_context(self, ctx: Context, turn: int) -> None:
        settings = self._settings
        if not settings.context_compress or settings.context_max_tokens <= 0:
            return
        summarize_fn = None
        if settings.context_compress_llm:
            summarize_fn = self._llm.summarize_transcript
        # Settings already clamp to CONTEXT_TOKEN_HARD_CAP (1M).
        info = ctx.maybe_compress(
            max_tokens=settings.context_max_tokens,
            keep_recent=settings.context_keep_recent,
            summarize_fn=summarize_fn,
        )
        if info is None:
            return
        info["turn"] = turn
        self._emit(Event("compress", info))

    def _emit_finish(
        self,
        summary: str,
        turn: int,
        usage: UsageTotals,
        run_started: float,
    ) -> None:
        wall_ms = (time.perf_counter() - run_started) * 1000
        payload = usage.as_dict()
        payload["wall_ms"] = round(wall_ms, 1)
        self._emit(Event("finish", {"summary": summary, "turn": turn, "usage": payload}))
        self._emit(Event("usage", payload))
