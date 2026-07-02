"""Built-in tools. Importing this module registers all tools on the shared
registry instance.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .registry import ToolRegistry

# A single shared registry for the built-in tool set.
registry = ToolRegistry()


@registry.tool(dangerous=True)
def run_shell(command: str) -> dict:
    """Execute a shell command and return its output.

    Use this to inspect the system, run programs, and accomplish tasks.
    The command runs in a real shell; chain with && or | as needed.

    :param command: The shell command line to execute.
    """
    # Timeout is injected by the executor via functools.partial-style config;
    # here we keep a safe default and let the executor override.
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=_SHELL_TIMEOUT[0],
        )
        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {_SHELL_TIMEOUT[0]}s",
        }


@registry.tool()
def read_file(path: str) -> dict:
    """Read a text file and return its contents with 1-indexed line numbers.

    :param path: Path to the file to read.
    """
    p = Path(path)
    if not p.exists():
        return {"success": False, "error": f"No such file: {path}"}
    if not p.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(lines, 1))
    return {"success": True, "total_lines": len(lines), "content": numbered}


@registry.tool()
def write_file(path: str, content: str) -> dict:
    """Write text to a file, creating parent directories and overwriting any
    existing content.

    :param path: Path to the file to write.
    :param content: The full text content to write.
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}
    return {"success": True, "bytes_written": len(content.encode("utf-8")), "path": str(p)}


@registry.tool()
def finish(summary: str) -> dict:
    """Declare the task complete. Call this ONLY when the user's task is fully
    accomplished. Provide a concise summary of what was done and the result.

    :param summary: A short summary of the outcome for the user.
    """
    return {"success": True, "finished": True, "summary": summary}


# Mutable holder so the executor can set the configured timeout without
# threading it through every call signature.
_SHELL_TIMEOUT = [60]


def set_shell_timeout(seconds: int) -> None:
    _SHELL_TIMEOUT[0] = seconds
