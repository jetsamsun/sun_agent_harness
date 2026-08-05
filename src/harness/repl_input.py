"""REPL input helpers: multi-line paste drain + explicit paste mode."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

# After the first line, wait this long for a paste buffer to keep filling.
_PASTE_SETTLE_S = 0.08
_PASTE_IDLE_S = 0.05

def stdin_has_pending() -> bool:
    """True if stdin likely has unread bytes/lines (non-blocking)."""
    if not sys.stdin.isatty():
        # Piped input: treat unread buffer as pending via peek where possible.
        try:
            return bool(sys.stdin.readable() and not sys.stdin.closed)
        except Exception:  # noqa: BLE001
            return False
    if sys.platform == "win32":
        try:
            import msvcrt

            return bool(msvcrt.kbhit())
        except Exception:  # noqa: BLE001
            return False
    try:
        import select

        return bool(select.select([sys.stdin], [], [], 0)[0])
    except Exception:  # noqa: BLE001
        return False


def drain_paste_lines(
    *,
    readline: Callable[[], str] | None = None,
    settle_s: float = _PASTE_SETTLE_S,
    idle_s: float = _PASTE_IDLE_S,
    has_pending: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Read additional lines already buffered from a multi-line paste."""
    readline = readline or sys.stdin.readline
    has_pending = has_pending or stdin_has_pending
    sleep(settle_s)
    lines: list[str] = []
    idle_rounds = 0
    while idle_rounds < 2:
        if not has_pending():
            idle_rounds += 1
            sleep(idle_s)
            continue
        idle_rounds = 0
        raw = readline()
        if raw == "":
            break
        lines.append(raw.rstrip("\r\n"))
    return lines


def _read_until_end(
    *,
    end_tokens: frozenset[str],
    read_line: Callable[[], str],
    first_body: str | None = None,
) -> str:
    parts: list[str] = []
    if first_body is not None and first_body != "":
        parts.append(first_body)
    while True:
        line = read_line()
        if line is None:
            break
        text = line.rstrip("\r\n")
        if text.strip() in end_tokens:
            break
        parts.append(text)
    # Trim one trailing empty line often left by paste.
    while parts and parts[-1] == "":
        parts.pop()
    return "\n".join(parts).strip()


def assemble_repl_message(
    first_line: str,
    *,
    continuation_lines: list[str] | None = None,
    explicit_body: str | None = None,
) -> str:
    """Combine first line + paste/continuation into one user message."""
    if explicit_body is not None:
        return explicit_body.strip()
    lines = [first_line.rstrip("\r\n")]
    if continuation_lines:
        lines.extend(continuation_lines)
    while len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def read_repl_message(
    *,
    prompt_line: Callable[[], str],
    cont_prompt_line: Callable[[], str] | None = None,
    readline: Callable[[], str] | None = None,
    has_pending: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Read one user turn: supports paste drain and /paste or \"\"\" fences."""
    readline = readline or sys.stdin.readline
    cont_prompt_line = cont_prompt_line or (
        lambda: sys.stdin.readline().rstrip("\r\n")
    )

    first = prompt_line()
    if first is None:
        return ""
    first = first.rstrip("\r\n")
    stripped = first.strip()

    # Explicit multi-line modes.
    if stripped.lower() in {"/paste", "/ml"}:
        body = _read_until_end(
            end_tokens=frozenset({"---", "/end"}),
            read_line=cont_prompt_line,
        )
        return body

    for fence in ('"""', "'''"):
        if stripped == fence:
            body = _read_until_end(
                end_tokens=frozenset({fence, "---", "/end"}),
                read_line=cont_prompt_line,
            )
            return body
        if stripped.startswith(fence):
            rest = stripped[len(fence) :]
            if rest.endswith(fence) and len(stripped) > 2 * len(fence):
                return rest[: -len(fence)].strip()
            body = _read_until_end(
                end_tokens=frozenset({fence, "---", "/end"}),
                read_line=cont_prompt_line,
                first_body=rest,
            )
            return body

    # Trailing backslash continuations (manual multi-line).
    if stripped.endswith("\\") and not stripped.endswith("\\\\"):
        parts = [stripped[:-1]]
        while True:
            nxt = cont_prompt_line()
            if nxt is None:
                break
            nxt = nxt.rstrip("\r\n")
            if nxt.strip().endswith("\\") and not nxt.strip().endswith("\\\\"):
                parts.append(nxt.rstrip().rstrip("\\"))
                continue
            parts.append(nxt)
            break
        return "\n".join(parts).strip()

    # Auto-drain pasted lines still sitting in the stdin buffer.
    extra = drain_paste_lines(
        readline=readline, has_pending=has_pending, sleep=sleep
    )
    return assemble_repl_message(first, continuation_lines=extra)
