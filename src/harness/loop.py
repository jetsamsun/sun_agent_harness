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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .context import Context
from .llm import LLMClient
from .tools.executor import ToolExecutor
from .tools.registry import ToolRegistry

SYSTEM_PROMPT = """You are Sun, an autonomous agent operating in a Linux shell.

You accomplish the user's task by calling tools. Work step by step:
inspect the environment, take an action, observe the result, then decide the
next step. Prefer small, verifiable steps over one giant command.

Rules:
- Use run_shell to execute commands, read_file / write_file for files.
- When you write code, RUN it to verify it works before declaring success.
- When the task is fully accomplished, call the `finish` tool with a concise
  summary. Do not call finish prematurely.
- If a task is impossible or unsafe, explain why via finish rather than
  guessing or fabricating results.
"""


@dataclass
class Event:
    kind: str  # "think" | "tool_call" | "tool_result" | "finish" | "error" | "stop"
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
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._executor = executor
        self._settings = settings
        self._emit = on_event or (lambda e: None)

    def run(self, task: str) -> str:
        ctx = Context(SYSTEM_PROMPT)
        ctx.add_user(task)
        tools = self._registry.openai_schemas()

        for turn in range(1, self._settings.max_turns + 1):
            message = self._llm.chat(ctx.messages(), tools=tools)
            ctx.add_assistant(message)

            tool_calls = getattr(message, "tool_calls", None)

            # No tool call: the model is talking to us. Treat text as final.
            if not tool_calls:
                text = message.content or ""
                self._emit(Event("finish", {"summary": text, "turn": turn}))
                return text

            if message.content:
                self._emit(Event("think", {"text": message.content, "turn": turn}))

            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments
                self._emit(Event("tool_call", {"name": name, "args": raw_args, "turn": turn}))

                result = self._executor.execute(name, raw_args)
                self._emit(Event("tool_result", {"name": name, "result": result}))

                ctx.add_tool_result(tc.id, json.dumps(result, ensure_ascii=False))

                if result.get("finished"):
                    summary = result.get("summary", "")
                    self._emit(Event("finish", {"summary": summary, "turn": turn}))
                    return summary

        self._emit(Event("stop", {"reason": "max_turns", "limit": self._settings.max_turns}))
        return f"[stopped] reached max turns ({self._settings.max_turns}) without finishing."
