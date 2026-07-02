"""Configuration loaded from environment / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the harness.

    Reads from environment variables (or a local .env file).
    All values prefixed with SUN_ to avoid collisions.
    """

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


def load_settings() -> Settings:
    return Settings()
