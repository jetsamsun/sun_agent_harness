"""Conversation context: the message stack plus a token estimate.

Stage 2④: REPL may reuse one Context across user lines (in-memory only).
Large tool payloads are clipped before append so sessions do not explode.
Rolling LLM compression is still deferred.
"""

from __future__ import annotations

from typing import Any

import tiktoken

# Soft cap for a single tool result string fed back to the model.
DEFAULT_TOOL_RESULT_CHARS = 12_000


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


class Context:
    def __init__(
        self,
        system_prompt: str,
        *,
        tool_result_chars: int = DEFAULT_TOOL_RESULT_CHARS,
    ) -> None:
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        self._tool_result_chars = tool_result_chars
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

    def token_estimate(self) -> int:
        if self._enc is None:
            return -1
        total = 0
        for m in self._messages:
            total += len(self._enc.encode(str(m.get("content", ""))))
        return total
