"""Thin wrapper around an OpenAI-compatible chat completions endpoint.

Supports optional streaming (content deltas + assembled tool_calls) and returns
token usage + latency for Stage 2⑥ observability.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from .config import Settings
from .trace import extract_usage

# Errors worth retrying — transient by nature. Bad requests / auth errors are
# deliberately NOT here: retrying them just wastes time and tokens.
_RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

# On /v1/chat/completions, gpt-5.3+ reject function tools unless
# reasoning_effort is explicitly "none" (or you migrate to /v1/responses).
_TOOLS_NEED_REASONING_NONE_PREFIXES = ("gpt-5.3", "gpt-5.4", "gpt-5.5", "gpt-5.6")

DeltaFn = Callable[[str], None]


def resolve_reasoning_effort(
    model: str, tools: list[dict[str, Any]] | None, configured: str
) -> str | None:
    """Pick reasoning_effort for a Chat Completions request, or None to omit."""
    if configured:
        return configured
    if not tools:
        return None
    name = model.lower()
    if name.startswith(_TOOLS_NEED_REASONING_NONE_PREFIXES):
        return "none"
    return None


@dataclass
class ChatResult:
    message: Any
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    streamed: bool = False


@dataclass
class _ToolCallBuilder:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class _StreamAssembler:
    content: str = ""
    tool_calls: dict[int, _ToolCallBuilder] = field(default_factory=dict)

    def feed(self, delta: Any) -> str:
        """Apply a delta; return any new content text for live display."""
        piece = ""
        if getattr(delta, "content", None):
            piece = delta.content
            self.content += piece
        for tc in getattr(delta, "tool_calls", None) or []:
            idx = int(getattr(tc, "index", 0) or 0)
            slot = self.tool_calls.setdefault(idx, _ToolCallBuilder())
            if getattr(tc, "id", None):
                slot.id = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot.name = fn.name
                if getattr(fn, "arguments", None):
                    slot.arguments += fn.arguments
        return piece

    def to_message(self) -> _AssembledMessage:
        calls = None
        if self.tool_calls:
            calls = [
                _AssembledToolCall(self.tool_calls[i])
                for i in sorted(self.tool_calls)
            ]
        return _AssembledMessage(self.content or None, calls)


@dataclass
class _AssembledFunction:
    name: str
    arguments: str


@dataclass
class _AssembledToolCall:
    id: str
    function: _AssembledFunction
    type: str = "function"

    def __init__(self, builder: _ToolCallBuilder) -> None:
        self.id = builder.id or "call_0"
        self.function = _AssembledFunction(builder.name, builder.arguments)
        self.type = "function"


@dataclass
class _AssembledMessage:
    content: str | None
    tool_calls: list[_AssembledToolCall] | None


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        on_delta: DeltaFn | None = None,
    ) -> ChatResult:
        """One round-trip (or stream) to the model, with exponential-backoff retry."""
        kwargs: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "temperature": self._settings.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        effort = resolve_reasoning_effort(
            self._settings.model, tools, self._settings.reasoning_effort
        )
        if effort is not None:
            kwargs["reasoning_effort"] = effort

        stream = bool(self._settings.streaming and on_delta is not None)
        attempts = max(1, self._settings.max_retries)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                if stream:
                    return self._chat_stream(kwargs, on_delta, started)
                return self._chat_once(kwargs, started)
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    break
                delay = min(2**attempt, 30)
                time.sleep(delay)
        raise RuntimeError(f"LLM call failed after {attempts} attempts: {last_exc}") from last_exc

    def _chat_once(self, kwargs: dict[str, Any], started: float) -> ChatResult:
        response = self._client.chat.completions.create(**kwargs)
        prompt, completion = extract_usage(response)
        return ChatResult(
            message=response.choices[0].message,
            prompt_tokens=prompt,
            completion_tokens=completion,
            latency_ms=(time.perf_counter() - started) * 1000,
            streamed=False,
        )

    def _chat_stream(
        self,
        kwargs: dict[str, Any],
        on_delta: DeltaFn,
        started: float,
    ) -> ChatResult:
        stream_kwargs = dict(kwargs)
        stream_kwargs["stream"] = True
        # Best-effort: many OpenAI-compatible servers honor this.
        stream_kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = self._client.chat.completions.create(**stream_kwargs)
        except TypeError:
            # Older SDKs / servers rejecting stream_options.
            stream_kwargs.pop("stream_options", None)
            stream = self._client.chat.completions.create(**stream_kwargs)

        assembler = _StreamAssembler()
        prompt = completion = 0
        for chunk in stream:
            p, c = extract_usage(chunk)
            if p or c:
                prompt, completion = p, c
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = assembler.feed(delta)
            if piece:
                on_delta(piece)

        return ChatResult(
            message=assembler.to_message(),
            prompt_tokens=prompt,
            completion_tokens=completion,
            latency_ms=(time.perf_counter() - started) * 1000,
            streamed=True,
        )
