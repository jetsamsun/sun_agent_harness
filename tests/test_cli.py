"""Tests for the CLI self-management layer: global config read/write and
the task-fallback command routing. No API key required.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the global config at a temp dir, clear SUN_* env vars, and run
    from a clean cwd so a project-local .env can't leak the real key in."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for k in list(os.environ):
        if k.startswith("SUN_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env here
    return tmp_path


def test_write_and_read_global_config(isolated_config):
    from harness.config_writer import read_config, write_config

    path = write_config({"api_key": "sk-abc", "base_url": "https://x/v1", "model": "m1"})
    assert Path(path).exists()
    data = read_config()
    assert data["api_key"] == "sk-abc"
    assert data["model"] == "m1"


def test_write_config_merges(isolated_config):
    from harness.config_writer import read_config, write_config

    write_config({"api_key": "sk-abc", "model": "m1"})
    write_config({"model": "m2"})  # partial update keeps api_key
    data = read_config()
    assert data["api_key"] == "sk-abc"
    assert data["model"] == "m2"


def test_global_config_feeds_settings(isolated_config, monkeypatch):
    from harness.config import load_settings
    from harness.config_writer import write_config

    # Ensure no real SUN_API_KEY leaks in from the developer's shell.
    monkeypatch.delenv("SUN_API_KEY", raising=False)
    write_config({"api_key": "sk-global", "model": "mm", "base_url": "https://b/v1"})
    s = load_settings()
    assert s.api_key == "sk-global"
    assert s.model == "mm"


def test_env_overrides_global(isolated_config, monkeypatch):
    from harness.config import load_settings
    from harness.config_writer import write_config

    write_config({"api_key": "sk-global"})
    monkeypatch.setenv("SUN_API_KEY", "sk-env")
    assert load_settings().api_key == "sk-env"


def test_version_command(isolated_config):
    from harness.__main__ import app

    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert "sun" in result.stdout


def test_config_command_masks_key(isolated_config):
    from harness.__main__ import app
    from harness.config_writer import write_config

    write_config({"api_key": "sk-secretsecret1234"})
    result = CliRunner().invoke(app, ["config"])
    assert result.exit_code == 0
    assert "sk-secretsecret1234" not in result.stdout  # masked


def test_model_command_non_interactive(isolated_config):
    from harness.__main__ import app
    from harness.config_writer import read_config

    result = CliRunner().invoke(
        app,
        ["model", "--key", "sk-viaflag", "--base-url", "https://f/v1", "--model", "mflag"],
    )
    assert result.exit_code == 0
    data = read_config()
    assert data["api_key"] == "sk-viaflag"
    assert data["base_url"] == "https://f/v1"
    assert data["model"] == "mflag"


def test_freeform_task_routes_to_run(isolated_config, monkeypatch):
    """`sun <freeform>` should inject `run` and reach run() (not error with
    'No such command'). We drive the real main() entry point via argv."""
    import harness.__main__ as m

    monkeypatch.delenv("SUN_API_KEY", raising=False)
    monkeypatch.setattr(m.sys, "argv", ["sun", "统计文件数量"])

    # No key configured → run() exits with code 1 after printing the hint.
    with pytest.raises(SystemExit):
        m.main()
    # argv should have had `run` injected as the subcommand.
    assert m.sys.argv[1] == "run"


def test_known_command_not_rerouted(isolated_config, monkeypatch):
    """A real subcommand like `version` must NOT get `run` injected."""
    import harness.__main__ as m

    monkeypatch.setattr(m.sys, "argv", ["sun", "version"])
    with pytest.raises(SystemExit):
        m.main()
    assert m.sys.argv[1] == "version"  # unchanged
