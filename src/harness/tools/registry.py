"""Tool registry: register python callables as LLM-callable tools.

A tool is a plain function decorated with @tool. We derive its JSON schema
from type hints + docstring, keep it deliberately simple for stage 1.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for arguments
    func: Callable[..., dict[str, Any]]
    dangerous: bool = False

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def tool(
        self,
        *,
        dangerous: bool = False,
    ) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
        """Decorator that registers a function as a tool.

        Parameter schema is inferred from type hints; parameter descriptions
        come from a simple `:param name: text` convention in the docstring.
        The description is the first docstring paragraph.
        """

        def decorator(func: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
            sig = inspect.signature(func)
            doc = inspect.getdoc(func) or ""
            summary, param_docs = _parse_docstring(doc)

            properties: dict[str, Any] = {}
            required: list[str] = []
            for pname, param in sig.parameters.items():
                ann = param.annotation
                json_type = _PY_TO_JSON.get(ann, "string")
                properties[pname] = {
                    "type": json_type,
                    "description": param_docs.get(pname, ""),
                }
                if param.default is inspect.Parameter.empty:
                    required.append(pname)

            self.register(
                Tool(
                    name=func.__name__,
                    description=summary,
                    parameters={
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                    func=func,
                    dangerous=dangerous,
                )
            )
            return func

        return decorator


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    lines = doc.splitlines()
    summary_lines: list[str] = []
    param_docs: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(":param"):
            # :param name: description
            try:
                _, rest = stripped.split("param", 1)
                name, desc = rest.split(":", 1)
                param_docs[name.strip()] = desc.strip()
            except ValueError:
                continue
        elif not param_docs:  # summary is everything before the first :param
            summary_lines.append(stripped)
    return " ".join(s for s in summary_lines if s).strip(), param_docs
