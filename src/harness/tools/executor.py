"""Tool executor: validates arguments, applies the safety gate, runs the
tool, and normalizes + truncates the result before it re-enters context.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..config import Settings
from ..safety import assess_command
from .registry import Tool, ToolRegistry

# A callback the CLI supplies to ask the user y/n. Returns True to proceed.
ConfirmFn = Callable[[str, str], bool]


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        settings: Settings,
        confirm_fn: ConfirmFn | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._confirm = confirm_fn

    def execute(self, name: str, raw_args: str) -> dict[str, Any]:
        tool = self._registry.get(name)
        if tool is None:
            return {"success": False, "error": f"Unknown tool: {name}"}

        try:
            args = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"Invalid JSON arguments: {exc}"}

        # Safety gate: shell commands get a dedicated dangerous-pattern check.
        gate = self._safety_gate(tool, args)
        if gate is not None:
            return gate

        try:
            result = tool.func(**args)
        except TypeError as exc:
            return {"success": False, "error": f"Bad arguments for {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

        return self._truncate(result)

    def _safety_gate(self, tool: Tool, args: dict[str, Any]) -> dict[str, Any] | None:
        if not self._settings.require_confirmation:
            return None
        if tool.name != "run_shell":
            return None
        command = str(args.get("command", ""))
        reason = assess_command(command)
        if reason is None:
            return None
        if self._confirm is None:
            return {
                "success": False,
                "error": f"Blocked dangerous operation ({reason}); no confirmation channel.",
            }
        approved = self._confirm(command, reason)
        if not approved:
            return {"success": False, "error": f"User declined: {reason}"}
        return None

    def _truncate(self, result: dict[str, Any]) -> dict[str, Any]:
        limit = self._settings.max_output_chars
        for key in ("stdout", "stderr", "content"):
            val = result.get(key)
            if isinstance(val, str) and len(val) > limit:
                head = val[:limit]
                result[key] = (
                    head + f"\n... [truncated {len(val) - limit} chars of {len(val)} total]"
                )
        return result
