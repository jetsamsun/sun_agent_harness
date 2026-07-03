"""Helpers to read/write the global config file for the `sun model` command.

The config is a flat TOML with unprefixed keys (api_key, base_url, model, ...).
Python's stdlib can read TOML (tomllib) but not write it, so we serialize the
handful of scalar keys ourselves.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .config import global_config_path

# Keys we let `sun model` manage, with human labels.
MANAGED_KEYS = ["api_key", "base_url", "model"]


def read_config() -> dict[str, Any]:
    path = global_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data.get("sun", data) if isinstance(data, dict) else {}


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # string: escape backslashes and double quotes
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_config(values: dict[str, Any]) -> Path:
    """Merge `values` into the existing config and write it back.

    Returns the path written.
    """
    current = read_config()
    current.update({k: v for k, v in values.items() if v is not None})

    path = global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Sun Agent Harness global config", "# Managed by `sun model`.", ""]
    for key, value in current.items():
        lines.append(f"{key} = {_toml_scalar(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Best-effort: restrict permissions since it holds an API key (POSIX only).
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass
    return path
