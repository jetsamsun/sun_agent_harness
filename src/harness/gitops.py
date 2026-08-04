"""Thin git helpers for Stage 2③ checkpoints / rollback / commit."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .workspace import workspace_root

# Last checkpoint SHA created by Sun in this process (task/session).
_LAST_CHECKPOINT: str | None = None
_AUTO_TAKEN: bool = False
# Off until CLI enables from settings — avoids test runs committing into a real repo.
_AUTO_ENABLED: bool = False


def set_auto_git_checkpoint(enabled: bool) -> None:
    global _AUTO_ENABLED
    _AUTO_ENABLED = enabled


def reset_git_state() -> None:
    """Clear per-task checkpoint bookkeeping (called with planning reset)."""
    global _LAST_CHECKPOINT, _AUTO_TAKEN
    _LAST_CHECKPOINT = None
    _AUTO_TAKEN = False


def last_checkpoint() -> str | None:
    return _LAST_CHECKPOINT


def find_git_root(start: Path | None = None) -> Path | None:
    cwd = start or Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def _run_git(root: Path, args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _head_sha(root: Path) -> str | None:
    proc = _run_git(root, ["rev-parse", "HEAD"])
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def create_checkpoint(message: str = "", *, root: Path | None = None) -> dict[str, Any]:
    """Snapshot current tree: commit dirty changes, else record clean HEAD."""
    global _LAST_CHECKPOINT
    start = root or workspace_root() or Path.cwd()
    git_root = find_git_root(start)
    if git_root is None:
        return {"success": False, "skipped": True, "error": "Not inside a git repository"}

    msg = (message or "sun: checkpoint before edits").strip()
    status = _run_git(git_root, ["status", "--porcelain"])
    if status.returncode != 0:
        return {
            "success": False,
            "error": status.stderr.strip() or "git status failed",
        }

    dirty = bool(status.stdout.strip())
    if dirty:
        add = _run_git(git_root, ["add", "-A"])
        if add.returncode != 0:
            return {"success": False, "error": add.stderr.strip() or "git add failed"}
        commit = _run_git(git_root, ["commit", "-m", msg])
        if commit.returncode != 0:
            err = (commit.stderr or commit.stdout or "git commit failed").strip()
            return {"success": False, "error": err}

    sha = _head_sha(git_root)
    if not sha:
        return {"success": False, "error": "Could not read HEAD after checkpoint"}

    _LAST_CHECKPOINT = sha
    return {
        "success": True,
        "sha": sha,
        "root": str(git_root),
        "committed_dirty": dirty,
        "message": msg if dirty else "clean working tree; checkpoint is current HEAD",
    }


def ensure_auto_checkpoint() -> dict[str, Any] | None:
    """On first mutation of a task, create a checkpoint if enabled.

    Returns the checkpoint result dict, or None if skipped (already taken /
    disabled).
    """
    global _AUTO_TAKEN
    if not _AUTO_ENABLED or _AUTO_TAKEN:
        return None
    _AUTO_TAKEN = True
    result = create_checkpoint("sun: auto checkpoint before edits")
    # Not-a-repo is fine — mark taken so we don't retry every edit.
    return result


def rollback_to_checkpoint(sha: str | None = None, *, root: Path | None = None) -> dict[str, Any]:
    """Hard-reset the repo to a checkpoint SHA (defaults to last Sun checkpoint)."""
    start = root or workspace_root() or Path.cwd()
    git_root = find_git_root(start)
    if git_root is None:
        return {"success": False, "error": "Not inside a git repository"}
    target = (sha or _LAST_CHECKPOINT or "").strip()
    if not target:
        return {
            "success": False,
            "error": "No checkpoint SHA; call git_checkpoint first or pass sha=",
        }
    proc = _run_git(git_root, ["reset", "--hard", target])
    if proc.returncode != 0:
        return {"success": False, "error": (proc.stderr or proc.stdout).strip()}
    # Hard reset keeps untracked files; drop them so post-checkpoint writes vanish.
    clean = _run_git(git_root, ["clean", "-fd"])
    if clean.returncode != 0:
        return {
            "success": False,
            "error": (clean.stderr or clean.stdout).strip() or "git clean failed",
            "sha": target,
        }
    return {
        "success": True,
        "sha": target,
        "root": str(git_root),
        "stdout": (proc.stdout + "\n" + clean.stdout).strip(),
    }


def commit_all(message: str, *, root: Path | None = None) -> dict[str, Any]:
    """Stage all changes and create a commit with the given message."""
    start = root or workspace_root() or Path.cwd()
    git_root = find_git_root(start)
    if git_root is None:
        return {"success": False, "error": "Not inside a git repository"}
    msg = message.strip()
    if not msg:
        return {"success": False, "error": "Commit message must be non-empty"}

    status = _run_git(git_root, ["status", "--porcelain"])
    if status.returncode != 0:
        return {"success": False, "error": status.stderr.strip() or "git status failed"}
    if not status.stdout.strip():
        sha = _head_sha(git_root)
        return {
            "success": True,
            "noop": True,
            "sha": sha,
            "message": "Nothing to commit (working tree clean)",
        }

    add = _run_git(git_root, ["add", "-A"])
    if add.returncode != 0:
        return {"success": False, "error": add.stderr.strip() or "git add failed"}
    commit = _run_git(git_root, ["commit", "-m", msg])
    if commit.returncode != 0:
        return {
            "success": False,
            "error": (commit.stderr or commit.stdout or "git commit failed").strip(),
        }
    sha = _head_sha(git_root)
    return {"success": True, "sha": sha, "root": str(git_root), "message": msg}
