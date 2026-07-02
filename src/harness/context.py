"""Conversation context: the message stack plus a token estimate.

Stage 1 keeps the full history in memory (no compression yet). We expose a
token count so we can warn / cap before hitting the model's limit.
"""

from __future__ import annotations

from typing import Any

import tiktoken


class Context:
    def __init__(self, system_prompt: str) -> None:
        self._messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
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
        self._messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})

    def messages(self) -> list[dict[str, Any]]:
        return self._messages

    def token_estimate(self) -> int:
        if self._enc is None:
            return -1
        total = 0
        for m in self._messages:
            total += len(self._enc.encode(str(m.get("content", ""))))
        return total
