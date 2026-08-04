"""Structured JSONL trace sink for Stage 2⑥."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Avoid importing loop (circular): callers pass Event-like objects with .kind/.data.
EventLike = Any
EventFn = Callable[[EventLike], None]


class TraceSink:
    """Append-only JSONL writer; also fans out to an optional live callback."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        on_event: EventFn | None = None,
    ) -> None:
        self._path = Path(path) if path else None
        self._on_event = on_event
        self._file = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._path.open("a", encoding="utf-8")

    @property
    def path(self) -> Path | None:
        return self._path

    def __call__(self, event: EventLike) -> None:
        if self._file is not None:
            row = {
                "ts": datetime.now(UTC).isoformat(),
                "kind": event.kind,
                "data": event.data,
            }
            self._file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._file.flush()
        if self._on_event is not None:
            self._on_event(event)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


def default_trace_path() -> Path:
    """Per-run file under .sun/traces/ (cwd-relative)."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(".sun") / "traces" / f"{stamp}.jsonl"


def extract_usage(response_or_chunk: Any) -> tuple[int, int]:
    """Read (prompt, completion) tokens from an OpenAI response/chunk if present."""
    usage = getattr(response_or_chunk, "usage", None)
    if usage is None:
        return 0, 0
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    return prompt, completion
