"""Editable persona file injected into the system prompt.

Lookup order (first hit wins):
  1. Settings / SUN_PERSONA_PATH
  2. {cwd}/.sun/PERSONA.md
  3. ~/.config/sun/PERSONA.md  (auto-created with a default template)

Reloaded on every AgentLoop.run so edits apply without restarting sun.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .config import global_config_path

_PERSONA_MAX_CHARS = 16_000

DEFAULT_PERSONA = """# Sun 人格（可随时编辑）

编辑本文件即可调整语气与偏好；下一轮任务会自动重新加载（无需重启 sun 进程也可，
REPL 里下一条用户消息生效）。

与安全 / 工具硬性规则冲突时，以硬性规则为准。

## 身份
你是 Sun，用户本机上的自主编程助手。

## 风格
- 简洁、直接，少客套与废话
- 默认用中文回复（用户要求其他语言时再切换）
- 先做后说；结论放前面

## 偏好
- （在此写下你的习惯，例如：偏好 uv、少建临时文件、回答偏技术细节……）
"""


def global_persona_path() -> Path:
    return global_config_path().parent / "PERSONA.md"


def project_persona_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".sun" / "PERSONA.md"


def resolve_persona_path(explicit: str = "", *, cwd: Path | None = None) -> Path:
    """Return the persona file path that should be used (may not exist yet)."""
    if explicit.strip():
        return Path(explicit.strip()).expanduser()
    project = project_persona_path(cwd)
    if project.is_file():
        return project
    return global_persona_path()


def ensure_persona_file(path: Path) -> bool:
    """Create path with DEFAULT_PERSONA if missing. Returns True if created."""
    if path.is_file():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_PERSONA, encoding="utf-8")
    return True


def load_persona_text(path: Path) -> str:
    """Read persona markdown; empty string if missing/unreadable."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) > _PERSONA_MAX_CHARS:
        text = text[:_PERSONA_MAX_CHARS] + "\n\n… [persona truncated]"
    return text


def load_persona_block(explicit: str = "", *, cwd: Path | None = None) -> tuple[str, Path]:
    """Ensure a persona file exists (global default) and return (block, path).

    `block` is ready to append to the system prompt (may be empty).
    """
    path = resolve_persona_path(explicit, cwd=cwd)
    # Only auto-seed the global default — never overwrite a project choice silently.
    if not path.is_file() and path == global_persona_path():
        ensure_persona_file(path)
    text = load_persona_text(path)
    if not text:
        return "", path
    block = (
        "\n## 人格（来自可编辑文件，每轮任务重新加载）\n"
        f"文件: {path}\n\n"
        f"{text}\n"
    )
    return block, path


def open_persona_in_editor(path: Path) -> None:
    """Open persona file in $EDITOR / $VISUAL, or the OS default editor."""
    ensure_persona_file(path)
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        subprocess.run([editor, str(path)], check=False)
        return
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
        return
    subprocess.run(["xdg-open", str(path)], check=False)
