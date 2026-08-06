"""Tool package. Importing this exposes the shared registry with all
built-in tools registered.
"""

from __future__ import annotations

from . import browser_tools as browser_tools  # noqa: F401 — register browser_* tools
from .builtins import (
    export_planning_state,
    fetch_model_status,
    import_planning_state,
    registry,
    reset_browser_on_new_session,
    reset_planning_state,
    set_ask_fn,
    set_confirm_edits,
    set_edit_confirm_fn,
    set_llm_config,
    set_plan_confirm_fn,
    set_secret_vault_config,
    set_session_store,
    set_shell_timeout,
    set_sqlite_path,
)
from .executor import ToolExecutor
from .registry import Tool, ToolRegistry

# Re-export browser config helper
from ..browser_session import set_browser_config

__all__ = [
    "registry",
    "set_shell_timeout",
    "set_llm_config",
    "set_sqlite_path",
    "set_session_store",
    "set_secret_vault_config",
    "set_browser_config",
    "fetch_model_status",
    "set_ask_fn",
    "set_plan_confirm_fn",
    "set_edit_confirm_fn",
    "set_confirm_edits",
    "reset_planning_state",
    "reset_browser_on_new_session",
    "export_planning_state",
    "import_planning_state",
    "ToolExecutor",
    "Tool",
    "ToolRegistry",
]
