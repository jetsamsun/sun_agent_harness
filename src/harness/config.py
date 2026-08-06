"""Configuration loaded from (in priority order, highest first):

    1. Environment variables (SUN_*)
    2. A .env file in the current working directory
    3. The global config at ~/.config/sun/config.toml (written by `sun model`)
    4. Field defaults

This lets `sun` work from any directory once `sun model` has been run once,
while still allowing a project-local .env or one-off env vars to override.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# Hard ceiling for input context (DeepSeek V4-class 1M windows).
CONTEXT_TOKEN_HARD_CAP = 1_000_000


def global_config_path() -> Path:
    """Location of the user-global config file.

    Honors XDG_CONFIG_HOME; falls back to ~/.config/sun/config.toml.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "sun" / "config.toml"


class _GlobalTomlSource(PydanticBaseSettingsSource):
    """Reads ~/.config/sun/config.toml as the lowest-priority source.

    The TOML stores keys WITHOUT the SUN_ prefix (api_key, base_url, model...),
    optionally nested under a [sun] table.
    """

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}
        path = global_config_path()
        if path.exists():
            inner = TomlConfigSettingsSource(settings_cls, toml_file=path)
            raw = inner()
            # Support an optional [sun] wrapper table.
            self._data = raw.get("sun", raw) if isinstance(raw, dict) else {}

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v is not None}


class Settings(BaseSettings):
    """Runtime configuration for the harness."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SUN_",
        extra="ignore",
    )

    # --- LLM connection (OpenAI-compatible) ---
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    # --- Agent loop controls ---
    max_turns: int = 25
    """Hard cap on reasoning<->tool iterations before force-stop."""

    temperature: float = 0.0

    reasoning_effort: str = ""
    """Optional Chat Completions `reasoning_effort` (e.g. none/low/medium/high).

    Empty = auto: for gpt-5.3+ models with tools, send `none` so function
    calling works on /v1/chat/completions. Set explicitly to override.
    """

    max_retries: int = 4
    """Retry attempts for transient LLM errors (network, rate limit, 5xx)."""

    # --- Tool execution ---
    shell_timeout: int = 60
    """Seconds before a shell command is killed."""

    max_output_chars: int = 8000
    """Tool output longer than this is truncated before re-entering context."""

    # --- Safety ---
    require_confirmation: bool = True
    """Whether dangerous operations prompt for y/n confirmation."""

    confirm_edits: bool = False
    """Show a unified diff and ask before write_file / edit_file.

    Default off for smooth local use; set true (env/config) when you want a gate.
    """

    auto_git_checkpoint: bool = True
    """Before the first file mutation of a task, commit/record a git checkpoint."""

    workspace_root: str = ""
    """Directory file writes must stay inside. Empty = process cwd at startup."""

    persona_path: str = ""
    """Path to PERSONA.md. Empty = {cwd}/.sun/PERSONA.md if present, else ~/.config/sun/PERSONA.md."""

    # --- Session persistence (Redis; optional) ---
    redis_url: str = ""
    """Redis URL for chat-session memory (clearable via prune). Empty = no session persist."""

    redis_prefix: str = "sun"
    """Key prefix for session keys in Redis."""

    sqlite_path: str = ""
    """SQLite durable memory path. Empty = ./long_memory.db (project-local, like .env)."""

    # --- Observability (Stage 2⑥) ---
    streaming: bool = True
    """Stream assistant text deltas to the CLI while the model thinks."""

    show_usage: bool = True
    """Print token / timing / estimated cost after each task."""

    trace_log: str = ""
    """JSONL trace path. Empty = write `.sun/traces/<timestamp>.jsonl` when enabled."""

    enable_trace: bool = True
    """Persist structured events (think / tool / turn / usage) to a JSONL file."""

    # --- Context compression (Stage 2④) ---
    context_compress: bool = True
    """Fold older turns into a summary when context exceeds the token budget."""

    context_max_tokens: int = 900_000
    """Compress when estimate exceeds this. Hard-capped at 1_000_000. 0 disables."""

    context_keep_recent: int = 24
    """How many recent messages to keep verbatim after a compress pass."""

    context_compress_llm: bool = True
    """Use a short LLM call to refine the summary; falls back to heuristic."""

    @field_validator("context_max_tokens")
    @classmethod
    def _clamp_context_max_tokens(cls, value: int) -> int:
        if value <= 0:
            return 0
        return min(int(value), CONTEXT_TOKEN_HARD_CAP)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Order = priority (first wins):
        # explicit init args > env > .env > global TOML > defaults.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _GlobalTomlSource(settings_cls),
        )


def load_settings() -> Settings:
    return Settings()
