"""Conversation context: the message stack plus a token estimate.

Stage 2④: REPL may reuse one Context; oversized tool payloads are clipped;
when the stack grows past a token budget we fold older turns into a short
summary (heuristic, optionally refined by an LLM) and keep a recent tail.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import tiktoken

# Soft cap for a single tool result string fed back to the model.
DEFAULT_TOOL_RESULT_CHARS = 12_000

SummarizeFn = Callable[[list[dict[str, Any]]], str]


def clip_text(text: str, limit: int = DEFAULT_TOOL_RESULT_CHARS) -> str:
    """Keep head + tail of oversized text; mark how many chars were dropped."""
    if limit <= 0 or len(text) <= limit:
        return text
    marker_budget = 80
    usable = max(limit - marker_budget, 64)
    head = usable // 2
    tail = usable - head
    dropped = len(text) - head - tail
    return f"{text[:head]}\n…[truncated {dropped} chars]…\n{text[-tail:]}"


def _message_text(m: dict[str, Any]) -> str:
    parts = [str(m.get("content") or "")]
    for tc in m.get("tool_calls") or []:
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        name = fn.get("name", "?")
        args = str(fn.get("arguments") or "")
        if len(args) > 200:
            args = args[:200] + "…"
        parts.append(f"{name}({args})")
    return "\n".join(p for p in parts if p)


def heuristic_summary(messages: list[dict[str, Any]], *, max_chars: int = 3500) -> str:
    """Fold old turns into a compact Chinese bullet list (no LLM)."""
    users: list[str] = []
    tools: list[str] = []
    notes: list[str] = []

    for m in messages:
        role = m.get("role")
        if role == "user":
            text = str(m.get("content") or "").strip().replace("\n", " ")
            if text.startswith("[先前对话摘要]"):
                notes.append(text[:800])
            elif text:
                users.append(text[:240])
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                name = str(fn.get("name") or "?")
                args = str(fn.get("arguments") or "")
                hint = ""
                try:
                    parsed = json.loads(args) if args else {}
                    if isinstance(parsed, dict):
                        path = parsed.get("path") or parsed.get("command")
                        if path:
                            hint = f" → {str(path)[:120]}"
                except json.JSONDecodeError:
                    pass
                tools.append(f"{name}{hint}")
            content = str(m.get("content") or "").strip()
            if content and not m.get("tool_calls"):
                notes.append(content[:200])
        elif role == "tool":
            raw = str(m.get("content") or "")
            ok = True
            err = ""
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    ok = bool(data.get("success", True))
                    err = str(data.get("error") or "")[:120]
            except json.JSONDecodeError:
                pass
            marker = "ok" if ok else f"fail:{err or 'error'}"
            tools.append(f"  ↳ {marker}")

    lines: list[str] = ["[先前对话摘要 — 自动压缩，细节可能省略]"]
    if notes:
        lines.append("既有摘要/结论:")
        for n in notes[-4:]:
            lines.append(f"- {n}")
    if users:
        lines.append("用户诉求:")
        for u in users[-6:]:
            lines.append(f"- {u}")
    if tools:
        lines.append("已调用工具（节选）:")
        # Deduplicate consecutive identical lines.
        prev = None
        kept = 0
        for t in tools[-40:]:
            if t == prev:
                continue
            lines.append(f"- {t}")
            prev = t
            kept += 1
            if kept >= 24:
                lines.append("- …")
                break
    text = "\n".join(lines)
    return clip_text(text, max_chars)


class Context:
    def __init__(
        self,
        system_prompt: str,
        *,
        tool_result_chars: int = DEFAULT_TOOL_RESULT_CHARS,
    ) -> None:
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        self._tool_result_chars = tool_result_chars
        self._compress_count = 0
        try:
            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001
            self._enc = None

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, message: Any) -> None:
        """Append a raw assistant message object (may carry tool_calls)."""
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
        self._messages.append(entry)

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": clip_text(content, self._tool_result_chars),
            }
        )

    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    def user_turns(self) -> int:
        return sum(1 for m in self._messages if m.get("role") == "user")

    def compress_count(self) -> int:
        return self._compress_count

    def token_estimate(self) -> int:
        if self._enc is None:
            # Fallback: rough chars/4 when tiktoken unavailable.
            total = sum(len(_message_text(m)) for m in self._messages)
            return max(1, total // 4)
        total = 0
        for m in self._messages:
            total += len(self._enc.encode(_message_text(m)))
        return total

    def maybe_compress(
        self,
        *,
        max_tokens: int,
        keep_recent: int = 24,
        summarize_fn: SummarizeFn | None = None,
    ) -> dict[str, Any] | None:
        """If over budget, fold older messages into one summary user turn.

        Keeps the system prompt and a recent tail (tool pairs left intact).
        Returns a small report dict when compression ran, else None.
        """
        if max_tokens <= 0:
            return None
        before = self.token_estimate()
        if before <= max_tokens:
            return None

        system = self._messages[0]
        body = self._messages[1:]
        if len(body) <= keep_recent + 2:
            # Already short: aggressively shrink tool payloads then re-check.
            self._shrink_tool_payloads(limit=800)
            after = self.token_estimate()
            if after >= before:
                return None
            self._compress_count += 1
            return {
                "method": "shrink_tools",
                "before_tokens": before,
                "after_tokens": after,
                "dropped_messages": 0,
                "kept_messages": len(self._messages) - 1,
            }

        keep_n = max(4, keep_recent)
        keep = list(body[-keep_n:])
        # Don't start the live window on a bare tool result (orphan tool_call_id).
        while keep and keep[0].get("role") == "tool":
            idx = len(body) - len(keep) - 1
            if idx < 0:
                break
            keep.insert(0, body[idx])
        # Prefer starting at a user turn when possible.
        while len(keep) < len(body) and keep and keep[0].get("role") not in (
            "user",
            "assistant",
        ):
            idx = len(body) - len(keep) - 1
            if idx < 0:
                break
            keep.insert(0, body[idx])

        drop_n = len(body) - len(keep)
        if drop_n <= 0:
            self._shrink_tool_payloads(limit=800)
            after = self.token_estimate()
            if after >= before:
                return None
            self._compress_count += 1
            return {
                "method": "shrink_tools",
                "before_tokens": before,
                "after_tokens": after,
                "dropped_messages": 0,
                "kept_messages": len(self._messages) - 1,
            }

        dropped = body[:drop_n]
        method = "heuristic"
        summary = heuristic_summary(dropped)
        if summarize_fn is not None:
            try:
                llm_summary = (summarize_fn(dropped) or "").strip()
                if llm_summary:
                    summary = llm_summary
                    if not summary.startswith("[先前对话摘要"):
                        summary = "[先前对话摘要]\n" + summary
                    method = "llm"
            except Exception:  # noqa: BLE001
                method = "heuristic_fallback"

        summary_msg = {"role": "user", "content": summary}
        self._messages = [system, summary_msg, *keep]
        # Also trim fat tool results in the kept window.
        self._shrink_tool_payloads(limit=min(self._tool_result_chars, 4000))
        after = self.token_estimate()
        self._compress_count += 1
        return {
            "method": method,
            "before_tokens": before,
            "after_tokens": after,
            "dropped_messages": drop_n,
            "kept_messages": len(keep),
        }

    def _shrink_tool_payloads(self, *, limit: int) -> None:
        for m in self._messages:
            if m.get("role") != "tool":
                continue
            content = m.get("content")
            if isinstance(content, str) and len(content) > limit:
                m["content"] = clip_text(content, limit)
