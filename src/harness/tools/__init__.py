"""Tool package. Importing this exposes the shared registry with all
built-in tools registered.
"""

from __future__ import annotations

from .builtins import (
    registry,
    reset_planning_state,
    set_ask_fn,
    set_confirm_edits,
    set_edit_confirm_fn,
    set_plan_confirm_fn,
    set_shell_timeout,
)
from .executor import ToolExecutor
from .registry import Tool, ToolRegistry

__all__ = [
    "registry",
    "set_shell_timeout",
    "set_ask_fn",
    "set_plan_confirm_fn",
    "set_edit_confirm_fn",
    "set_confirm_edits",
    "reset_planning_state",
    "ToolExecutor",
    "Tool",
    "ToolRegistry",
]
