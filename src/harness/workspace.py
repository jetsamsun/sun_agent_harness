"""Workspace sandbox and unified diffs for file mutations.

Stage 2③: keep writes inside a project root and preview edits as diffs.
"""

from __future__ import annotations

import difflib
from pathlib import Path

# None = unrestricted (tests / before CLI configures). Path = sandbox root.
_WORKSPACE_ROOT: list[Path | None] = [None]


def set_workspace_root(root: str | Path | None) -> None:
    """Set the directory that file writes must stay inside. None disables."""
    if root is None or root == "":
        _WORKSPACE_ROOT[0] = None
        return
    _WORKSPACE_ROOT[0] = Path(root).expanduser().resolve()


def workspace_root() -> Path | None:
    return _WORKSPACE_ROOT[0]


def resolve_in_workspace(path: str) -> tuple[Path | None, str | None]:
    """Resolve path; if a workspace root is set, reject escapes outside it.

    Relative paths are resolved against the workspace root when set, else cwd.
    """
    raw = Path(path).expanduser()
    root = _WORKSPACE_ROOT[0]
    if raw.is_absolute():
        resolved = raw.resolve()
    elif root is not None:
        resolved = (root / raw).resolve()
    else:
        resolved = (Path.cwd() / raw).resolve()

    if root is not None:
        try:
            resolved.relative_to(root)
        except ValueError:
            return None, f"Path escapes workspace ({root}): {path}"
    return resolved, None


def unified_diff(path: str | Path, old: str, new: str, *, max_lines: int = 200) -> str:
    """Return a unified diff string (possibly truncated)."""
    label = str(path)
    lines = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )
    if not lines:
        return "(no changes)"
    text = "".join(lines)
    body = text.splitlines()
    if len(body) > max_lines:
        kept = body[: max_lines - 1]
        text = "\n".join(kept) + f"\n…[{len(body) - len(kept)} more diff lines truncated]"
    return text
