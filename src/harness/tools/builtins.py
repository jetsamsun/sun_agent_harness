"""Built-in tools. Importing this module registers all tools on the shared
registry instance.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .. import gitops
from ..workspace import resolve_in_workspace, unified_diff
from .registry import ToolRegistry

# A single shared registry for the built-in tool set.
registry = ToolRegistry()

# Interactive callbacks injected by the CLI (tests can stub these).
AskFn = Callable[[str], str]
PlanConfirmFn = Callable[[str, list[dict[str, Any]]], tuple[bool, str]]
# (path, diff) -> approved
EditConfirmFn = Callable[[str, str], bool]
_ASK_FN: list[AskFn | None] = [None]
_PLAN_CONFIRM_FN: list[PlanConfirmFn | None] = [None]
_EDIT_CONFIRM_FN: list[EditConfirmFn | None] = [None]
_CONFIRM_EDITS: list[bool] = [False]

# Per-task planning state (reset at the start of each AgentLoop.run).
_TODOS: list[dict[str, Any]] = []
_PLAN: dict[str, Any] | None = None

_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
}


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
            encoding="utf-8",
            errors="replace",
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


def _as_int(value: object, default: int) -> int:
    """Coerce tool args that models often send as JSON strings."""
    if value is None or value == "":
        return default
    return int(value)  # type: ignore[arg-type]


@registry.tool()
def read_file(path: str, offset: int = 1, limit: int = 0) -> dict:
    """Read a text file with 1-indexed line numbers.

    Use offset/limit to read a slice of large files (limit=0 means read to end).

    :param path: Path to the file to read.
    :param offset: 1-indexed start line (default 1).
    :param limit: Max number of lines to return; 0 means all remaining lines.
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

    try:
        start = max(1, _as_int(offset, 1))
        lim = _as_int(limit, 0)
    except (TypeError, ValueError) as exc:
        return {"success": False, "error": f"offset/limit must be integers: {exc}"}

    total = len(lines)
    if start > total and total > 0:
        return {
            "success": False,
            "error": f"offset {start} past end of file ({total} lines)",
            "total_lines": total,
        }
    begin = start - 1
    end = total if lim <= 0 else min(total, begin + lim)
    chunk = lines[begin:end]
    numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(chunk, start))
    return {
        "success": True,
        "total_lines": total,
        "offset": start,
        "limit": lim,
        "start_line": start if chunk else 0,
        "end_line": start + len(chunk) - 1 if chunk else 0,
        "content": numbered,
    }


def _apply_file_mutation(path: str, new_content: str) -> dict:
    """Shared path sandbox + diff confirm + auto checkpoint + write."""
    p, err = resolve_in_workspace(path)
    if err or p is None:
        return {"success": False, "error": err or "Invalid path"}

    old = ""
    if p.exists():
        if not p.is_file():
            return {"success": False, "error": f"Not a file: {path}"}
        try:
            old = p.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

    if old == new_content:
        return {
            "success": True,
            "path": str(p),
            "bytes_written": len(new_content.encode("utf-8")),
            "noop": True,
            "diff": "(no changes)",
        }

    diff = unified_diff(p, old, new_content)
    if _CONFIRM_EDITS[0]:
        if _EDIT_CONFIRM_FN[0] is None:
            return {
                "success": False,
                "error": "Edit confirmation required but no confirmation channel",
                "path": str(p),
                "diff": diff,
            }
        if not _EDIT_CONFIRM_FN[0](str(p), diff):
            return {
                "success": False,
                "error": "User declined this edit",
                "path": str(p),
                "diff": diff,
            }

    checkpoint = gitops.ensure_auto_checkpoint()

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new_content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}

    out: dict[str, Any] = {
        "success": True,
        "path": str(p),
        "bytes_written": len(new_content.encode("utf-8")),
        "diff": diff,
    }
    if checkpoint is not None:
        out["checkpoint"] = {
            "success": checkpoint.get("success"),
            "sha": checkpoint.get("sha"),
            "skipped": checkpoint.get("skipped"),
            "message": checkpoint.get("message") or checkpoint.get("error"),
        }
    return out


@registry.tool()
def write_file(path: str, content: str) -> dict:
    """Write text to a file, creating parent directories and overwriting any
    existing content. Prefer edit_file for small changes to existing files.

    Paths must stay inside the workspace root. May show a diff for confirmation
    and auto-create a git checkpoint before the first edit of a task.

    :param path: Path to the file to write.
    :param content: The full text content to write.
    """
    return _apply_file_mutation(path, content)


@registry.tool()
def edit_file(path: str, old_text: str, new_text: str) -> dict:
    """Replace exactly one occurrence of old_text with new_text in a file.

    old_text must match uniquely — if zero or multiple matches, the edit fails
    so you can re-read and try a more specific snippet. Prefer this over
    write_file when changing existing code.

    :param path: Path to the file to edit.
    :param old_text: Exact text to find (must appear exactly once).
    :param new_text: Replacement text.
    """
    p, err = resolve_in_workspace(path)
    if err or p is None:
        return {"success": False, "error": err or "Invalid path"}
    if not p.exists():
        return {"success": False, "error": f"No such file: {path}"}
    if not p.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    try:
        original = p.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}

    count = original.count(old_text)
    if count == 0:
        return {
            "success": False,
            "error": "old_text not found; re-read the file and use an exact snippet",
        }
    if count > 1:
        return {
            "success": False,
            "error": f"old_text matched {count} times; provide a more unique snippet",
            "matches": count,
        }
    updated = original.replace(old_text, new_text, 1)
    result = _apply_file_mutation(str(p), updated)
    if result.get("success"):
        result["replacements"] = 1
    return result


@registry.tool()
def search_files(pattern: str, path: str = ".", glob: str = "*") -> dict:
    """Search file contents with a regular expression (like grep).

    Skips common junk dirs (.git, .venv, node_modules, …). Returns matching
    lines with path and 1-indexed line numbers.

    :param pattern: Regular expression to search for.
    :param path: Root directory to search (default current directory).
    :param glob: Filename glob filter, e.g. "*.py" (default all files).
    """
    root = Path(path)
    if not root.exists():
        return {"success": False, "error": f"No such path: {path}"}
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}

    matches: list[dict[str, object]] = []
    files_scanned = 0
    max_matches = 50

    for file_path in _iter_files(root, glob):
        files_scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append({"path": str(file_path), "line": i, "text": line[:300]})
                if len(matches) >= max_matches:
                    return {
                        "success": True,
                        "files_scanned": files_scanned,
                        "match_count": len(matches),
                        "truncated": True,
                        "matches": matches,
                    }

    return {
        "success": True,
        "files_scanned": files_scanned,
        "match_count": len(matches),
        "truncated": False,
        "matches": matches,
    }


@registry.tool()
def find_files(pattern: str, path: str = ".") -> dict:
    """Find files by name glob pattern (e.g. "*.py", "**/test_*.py").

    :param pattern: Glob pattern relative to path.
    :param path: Root directory to search (default current directory).
    """
    root = Path(path)
    if not root.exists():
        return {"success": False, "error": f"No such path: {path}"}

    hits: list[str] = []
    max_hits = 200
    for file_path in root.rglob(pattern):
        if not file_path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in file_path.parts):
            continue
        hits.append(str(file_path))
        if len(hits) >= max_hits:
            return {
                "success": True,
                "count": len(hits),
                "truncated": True,
                "files": hits,
            }
    return {"success": True, "count": len(hits), "truncated": False, "files": hits}


@registry.tool()
def list_dir(path: str = ".") -> dict:
    """List files and subdirectories in a directory (non-recursive).

    :param path: Directory to list (default current directory).
    """
    p = Path(path)
    if not p.exists():
        return {"success": False, "error": f"No such path: {path}"}
    if not p.is_dir():
        return {"success": False, "error": f"Not a directory: {path}"}

    entries: list[dict[str, str]] = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name in _SKIP_DIR_NAMES:
                continue
            kind = "dir" if child.is_dir() else "file"
            entries.append({"name": child.name, "type": kind})
    except OSError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "path": str(p.resolve()), "entries": entries}


@registry.tool()
def check_syntax(path: str) -> dict:
    """Check that a source file parses without running it.

    Supports .py (python -m py_compile). Use after write_file/edit_file.

    :param path: Path to the source file.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {"success": False, "passed": False, "error": f"No such file: {path}"}

    suffix = p.suffix.lower()
    if suffix == ".py":
        cmd = f'python -m py_compile "{p}"'
    elif suffix in {".js", ".mjs", ".cjs"}:
        cmd = f'node --check "{p}"'
    else:
        return {
            "success": False,
            "passed": False,
            "error": f"No syntax checker wired for {suffix or '(no extension)'}",
        }

    proc = _run_capture(cmd, timeout=60)
    passed = proc["exit_code"] == 0
    return {
        "success": passed,
        "passed": passed,
        "path": str(p),
        "command": cmd,
        "stdout": proc["stdout"],
        "stderr": proc["stderr"],
        "exit_code": proc["exit_code"],
    }


@registry.tool()
def run_tests(command: str = "") -> dict:
    """Run the project's test suite and return a structured pass/fail summary.

    If command is empty, auto-detect (pytest / npm test / go test). On failure,
    fix the code and call run_tests again — do not finish while tests are red.

    :param command: Explicit test command; leave empty to auto-detect.
    """
    cwd = Path.cwd()
    cmd = command.strip() or _detect_test_command(cwd)
    if not cmd:
        return {
            "success": False,
            "passed": False,
            "error": "Could not auto-detect tests; pass an explicit command",
        }

    proc = _run_capture(cmd, timeout=max(_SHELL_TIMEOUT[0], 120))
    passed = proc["exit_code"] == 0
    output = _join_output(proc["stdout"], proc["stderr"])
    return {
        "success": passed,
        "passed": passed,
        "command": cmd,
        "exit_code": proc["exit_code"],
        "summary": _test_summary(output, passed),
        "output": output[:6000],
    }


@registry.tool()
def run_lint(command: str = "") -> dict:
    """Run a linter / static checker (ruff, eslint, …).

    If command is empty, auto-detect a sensible default for the repo.

    :param command: Explicit lint command; leave empty to auto-detect.
    """
    cwd = Path.cwd()
    cmd = command.strip() or _detect_lint_command(cwd)
    if not cmd:
        return {
            "success": False,
            "passed": False,
            "error": "Could not auto-detect a linter; pass an explicit command",
        }

    proc = _run_capture(cmd, timeout=max(_SHELL_TIMEOUT[0], 120))
    passed = proc["exit_code"] == 0
    output = _join_output(proc["stdout"], proc["stderr"])
    return {
        "success": passed,
        "passed": passed,
        "command": cmd,
        "exit_code": proc["exit_code"],
        "output": output[:6000],
    }


@registry.tool()
def ask_user(question: str) -> dict:
    """Ask the human a clarifying question and wait for their reply.

    Use when the task is ambiguous (scope, acceptance criteria, stack, paths).
    Prefer one focused question at a time.

    :param question: The question to show the user.
    """
    if not sys.stdin.isatty() or _ASK_FN[0] is None:
        return {
            "success": False,
            "error": "No interactive TTY for ask_user; cannot clarify",
        }
    answer = _ASK_FN[0](question)
    return {"success": True, "question": question, "answer": answer}


@registry.tool()
def propose_plan(title: str, steps_json: str) -> dict:
    """Propose a multi-step plan with per-step acceptance criteria for approval.

    steps_json must be a JSON array of objects:
    [{"id":"1","title":"...","acceptance":"..."}, ...]
    Do not start coding edits until approved=true. If rejected, revise and call
    propose_plan again (or ask_user for more constraints).

    :param title: Short plan title.
    :param steps_json: JSON array of steps with id/title/acceptance.
    """
    global _PLAN
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return {"success": False, "approved": False, "error": f"Invalid steps_json: {exc}"}
    if not isinstance(steps, list) or not steps:
        return {
            "success": False,
            "approved": False,
            "error": "steps_json must be a non-empty array",
        }

    cleaned: list[dict[str, Any]] = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            return {"success": False, "approved": False, "error": f"step {i} must be an object"}
        cleaned.append(
            {
                "id": str(step.get("id") or i),
                "title": str(step.get("title") or "").strip() or f"Step {i}",
                "acceptance": str(step.get("acceptance") or "").strip() or "(unspecified)",
            }
        )

    if not sys.stdin.isatty() or _PLAN_CONFIRM_FN[0] is None:
        return {
            "success": False,
            "approved": False,
            "error": "No interactive TTY for propose_plan; cannot get approval",
            "title": title,
            "steps": cleaned,
        }

    approved, note = _PLAN_CONFIRM_FN[0](title, cleaned)
    _PLAN = {
        "title": title,
        "steps": cleaned,
        "approved": approved,
        "note": note,
    }
    return {
        "success": True,
        "approved": approved,
        "note": note,
        "title": title,
        "steps": cleaned,
        "hint": (
            "Plan approved — mirror steps into todo_write and execute."
            if approved
            else "Plan rejected — revise via ask_user/propose_plan before editing."
        ),
    }


@registry.tool()
def todo_write(items_json: str) -> dict:
    """Replace the in-memory todo list for this task (progress checklist).

    items_json: JSON array of
    {"id":"1","content":"...","status":"pending|in_progress|done|cancelled"}
    Keep exactly one item in_progress. Call after plan approval and as you go.

    :param items_json: JSON array of todo items.
    """
    global _TODOS
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"Invalid items_json: {exc}"}
    if not isinstance(items, list):
        return {"success": False, "error": "items_json must be a JSON array"}

    allowed = {"pending", "in_progress", "done", "cancelled"}
    cleaned: list[dict[str, Any]] = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return {"success": False, "error": f"item {i} must be an object"}
        status = str(item.get("status") or "pending").lower()
        if status not in allowed:
            return {"success": False, "error": f"item {i}: invalid status {status}"}
        cleaned.append(
            {
                "id": str(item.get("id") or i),
                "content": str(item.get("content") or "").strip() or f"Item {i}",
                "status": status,
            }
        )

    in_progress = sum(1 for t in cleaned if t["status"] == "in_progress")
    if in_progress > 1:
        return {"success": False, "error": "At most one todo may be in_progress"}

    _TODOS = cleaned
    done = sum(1 for t in cleaned if t["status"] == "done")
    return {
        "success": True,
        "count": len(cleaned),
        "done": done,
        "todos": cleaned,
    }


@registry.tool()
def git_checkpoint(message: str = "") -> dict:
    """Create a git checkpoint (commit dirty changes, or record clean HEAD).

    Call before risky edits if you want an explicit restore point. The harness
    also auto-checkpoints once before the first write/edit of a task when enabled.

    :param message: Optional commit message for a dirty-tree checkpoint.
    """
    return gitops.create_checkpoint(message)


@registry.tool(dangerous=True)
def git_rollback(sha: str = "") -> dict:
    """Hard-reset the repo to a checkpoint SHA (default: last Sun checkpoint).

    Destructive — reset --hard + clean -fd: discards later commits, dirty and
    untracked files. Prefer after a bad edit series. May require confirmation.

    :param sha: Checkpoint SHA; empty uses the last auto/manual Sun checkpoint.
    """
    return gitops.rollback_to_checkpoint(sha or None)


@registry.tool()
def git_commit(message: str) -> dict:
    """Stage all changes and create a git commit with the given message.

    Use after verification is green when the user asked to save/commit work.

    :param message: Commit message (required, non-empty).
    """
    return gitops.commit_all(message)


@registry.tool()
def finish(summary: str) -> dict:
    """Declare the task complete. Call this ONLY when the user's task is fully
    accomplished and verification is green (tests/syntax as applicable).

    :param summary: A short summary of the outcome for the user.
    """
    return {"success": True, "finished": True, "summary": summary}


def set_ask_fn(fn: AskFn | None) -> None:
    _ASK_FN[0] = fn


def set_plan_confirm_fn(fn: PlanConfirmFn | None) -> None:
    _PLAN_CONFIRM_FN[0] = fn


def set_edit_confirm_fn(fn: EditConfirmFn | None) -> None:
    _EDIT_CONFIRM_FN[0] = fn


def set_confirm_edits(enabled: bool) -> None:
    _CONFIRM_EDITS[0] = enabled


def reset_planning_state() -> None:
    """Clear todos/plan/git bookkeeping between tasks (called by AgentLoop.run)."""
    global _TODOS, _PLAN
    _TODOS = []
    _PLAN = None
    gitops.reset_git_state()


def _run_capture(command: str, timeout: int) -> dict:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }


def _join_output(stdout: str, stderr: str) -> str:
    parts = [p for p in (stdout.strip(), stderr.strip()) if p]
    return "\n".join(parts)


def _detect_test_command(cwd: Path) -> str | None:
    if (cwd / "pytest.ini").exists() or (cwd / "conftest.py").exists():
        return "python -m pytest -q"
    if (cwd / "pyproject.toml").exists():
        text = (cwd / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        if "pytest" in text or (cwd / "tests").is_dir():
            return "python -m pytest -q"
    if (cwd / "package.json").exists():
        text = (cwd / "package.json").read_text(encoding="utf-8", errors="replace")
        if '"test"' in text:
            return "npm test --silent"
    if (cwd / "go.mod").exists():
        return "go test ./..."
    if any(cwd.glob("test_*.py")) or any(cwd.glob("*_test.py")):
        return "python -m pytest -q"
    return None


def _detect_lint_command(cwd: Path) -> str | None:
    if (cwd / "pyproject.toml").exists() or (cwd / "ruff.toml").exists():
        text = ""
        cfg = cwd / "pyproject.toml"
        if cfg.exists():
            text = cfg.read_text(encoding="utf-8", errors="replace")
        if "ruff" in text or (cwd / "ruff.toml").exists():
            return "python -m ruff check ."
    if (cwd / "package.json").exists():
        text = (cwd / "package.json").read_text(encoding="utf-8", errors="replace")
        if '"lint"' in text:
            return "npm run lint --silent"
    return None


def _test_summary(output: str, passed: bool) -> str:
    if passed:
        return "PASS"
    lines = [ln for ln in output.splitlines() if ln.strip()]
    # Prefer pytest failure headers / assertion lines.
    interesting = [
        ln
        for ln in lines
        if ln.startswith("FAILED")
        or ln.startswith("ERROR")
        or "AssertionError" in ln
        or ln.strip().startswith("E ")
    ]
    if interesting:
        return "FAIL: " + " | ".join(interesting[:8])
    tail = lines[-12:] if lines else ["(no output)"]
    return "FAIL: " + " | ".join(tail)


def _iter_files(root: Path, glob_pat: str) -> Iterator[Path]:
    if root.is_file():
        if fnmatch.fnmatch(root.name, glob_pat):
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for name in filenames:
            if not fnmatch.fnmatch(name, glob_pat):
                continue
            yield Path(dirpath) / name


# Mutable holder so the executor can set the configured timeout without
# threading it through every call signature.
_SHELL_TIMEOUT = [60]


def set_shell_timeout(seconds: int) -> None:
    _SHELL_TIMEOUT[0] = seconds
