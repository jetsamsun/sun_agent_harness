"""Tool package. Importing this exposes the shared registry with all
built-in tools registered.
"""

from __future__ import annotations

from .builtins import (
    export_planning_state,
    fetch_model_status,
    import_planning_state,
    registry,
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

__all__ = [
    "registry",
    "set_shell_timeout",
    "set_llm_config",
    "set_sqlite_path",
    "set_session_store",
    "set_secret_vault_config",
    "fetch_model_status",
    "set_ask_fn",
    "set_plan_confirm_fn",
    "set_edit_confirm_fn",
    "set_confirm_edits",
    "reset_planning_state",
    "export_planning_state",
    "import_planning_state",
    "ToolExecutor",
    "Tool",
    "ToolRegistry",
]
