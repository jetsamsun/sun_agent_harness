"""Thin wrapper around an OpenAI-compatible chat completions endpoint.

First stage: single provider, native function calling. No fallback, no
streaming — just a clean chat() that speaks tool_calls, with retry/backoff
on transient errors (network blips, rate limits, 5xx).
"""

from __future__ import annotations

import time
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from .config import Settings

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


def resolve_reasoning_effort(model: str, tools: list[dict[str, Any]] | None, configured: str) -> str | None:
    """Pick reasoning_effort for a Chat Completions request, or None to omit."""
    if configured:
        return configured
    if not tools:
        return None
    name = model.lower()
    if name.startswith(_TOOLS_NEED_REASONING_NONE_PREFIXES):
        return "none"
    return None


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
    ) -> Any:
        """One round-trip to the model, with exponential-backoff retry.

        Returns the raw `message` object from the first choice, which may
        contain `.content` and/or `.tool_calls`.
        """
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

        attempts = max(1, self._settings.max_retries)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._client.chat.completions.create(**kwargs)
                return response.choices[0].message
            except _RETRYABLE as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    break
                # 1s, 2s, 4s, ... capped at 30s.
                delay = min(2**attempt, 30)
                time.sleep(delay)
        raise RuntimeError(f"LLM call failed after {attempts} attempts: {last_exc}") from last_exc
