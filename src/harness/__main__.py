"""CLI entry point.

Commands:
    sun "task"            run a task (shorthand for `sun run`)
    sun run "task"        run a task; omit task for interactive REPL
    sun model             configure the LLM (api key / base url / model)
    sun config            show current effective configuration
    sun update            reinstall the latest version from GitHub
    sun remove            uninstall sun
    sun help / --help     show help
    sun version           show version
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import typer
from click.exceptions import UsageError
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from typer.core import TyperGroup

from . import __version__
from .config import global_config_path, load_settings
from .config_writer import read_config, write_config
from .llm import LLMClient
from .loop import AgentLoop, Event
from .tools import ToolExecutor, registry, set_shell_timeout

GITHUB_SPEC = "git+https://github.com/jetsamsun/sun_agent_harness.git"


class _TaskFallbackGroup(TyperGroup):
    """A click Group that treats an unknown first token as a free-form task.

    `sun "统计文件数"` → routes to the `run` command with the whole string as
    the task, instead of erroring with "No such command".
    """

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except UsageError:
            # Not a known subcommand: hand everything to `run` as one task.
            cmd = self.get_command(ctx, "run")
            task = " ".join(args)
            return "run", cmd, [task]


app = typer.Typer(
    add_completion=False,
    help="Sun Agent Harness — a minimal agent for your terminal.",
    no_args_is_help=False,
    cls=_TaskFallbackGroup,
)
console = Console()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _make_event_printer():
    def printer(event: Event) -> None:
        if event.kind == "think":
            console.print(f"[dim]💭 {event.data['text']}[/dim]")
        elif event.kind == "tool_call":
            args = event.data["args"]
            try:
                args = json.dumps(json.loads(args), ensure_ascii=False)
            except Exception:  # noqa: BLE001
                pass
            console.print(f"[cyan]→ {event.data['name']}[/cyan] [dim]{args}[/dim]")
        elif event.kind == "tool_result":
            result = event.data["result"]
            ok = result.get("success")
            marker = "[green]✓[/green]" if ok else "[red]✗[/red]"
            preview = (
                result.get("stdout")
                or result.get("content")
                or result.get("error")
                or result.get("summary")
                or ""
            )
            preview = str(preview).strip()
            if len(preview) > 500:
                preview = preview[:500] + " …"
            console.print(f"  {marker} [dim]{preview}[/dim]")
        elif event.kind == "finish":
            console.print(Panel(event.data["summary"], title="✅ Done", border_style="green"))
        elif event.kind == "stop":
            console.print(
                Panel(
                    f"Stopped: {event.data['reason']}",
                    title="⏹ Stopped",
                    border_style="yellow",
                )
            )

    return printer


def _make_confirm_fn():
    def confirm(command: str, reason: str) -> bool:
        console.print(
            Panel(
                f"[bold]{command}[/bold]\n\n[yellow]Reason: {reason}[/yellow]",
                title="⚠ Dangerous operation",
                border_style="red",
            )
        )
        if not sys.stdin.isatty():
            console.print("[yellow]No interactive TTY — declining dangerous operation.[/yellow]")
            return False
        return Confirm.ask("Proceed?", default=False)

    return confirm


def _build_loop() -> AgentLoop:
    settings = load_settings()
    if not settings.api_key:
        console.print("[red]No API key configured.[/red] Run [bold]sun model[/bold] to set it up.")
        raise typer.Exit(1)
    set_shell_timeout(settings.shell_timeout)
    llm = LLMClient(settings)
    executor = ToolExecutor(registry, settings, confirm_fn=_make_confirm_fn())
    return AgentLoop(llm, registry, executor, settings, on_event=_make_event_printer())


def _run_task(task: str | None) -> None:
    loop = _build_loop()
    if task:
        loop.run(task)
        return
    console.print("[bold]Sun[/bold] — interactive mode. Ctrl-C to exit.")
    while True:
        try:
            task = console.input("[bold cyan]sun>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye")
            raise typer.Exit(0) from None
        if task:
            loop.run(task)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@app.command()
def run(task: str = typer.Argument(None, help="Task for the agent. Omit for REPL.")) -> None:
    """Run a task (or start the interactive REPL)."""
    _run_task(task)


@app.command()
def model(
    api_key: str = typer.Option(None, "--key", help="API key (non-interactive)."),
    base_url: str = typer.Option(None, "--base-url", help="Endpoint URL (non-interactive)."),
    model_name: str = typer.Option(None, "--model", help="Model name (non-interactive)."),
) -> None:
    """Configure the LLM: API key, base URL, and model name.

    Interactive by default. Pass --key/--base-url/--model to set values
    non-interactively (useful for scripts / CI / no-TTY environments).
    """
    current = read_config()

    # Non-interactive path: any flag given, or no TTY available.
    non_interactive = any([api_key, base_url, model_name]) or not sys.stdin.isatty()

    if non_interactive:
        values = {}
        if base_url:
            values["base_url"] = base_url
        if model_name:
            values["model"] = model_name
        if api_key:
            values["api_key"] = api_key
        if not values:
            console.print(
                "[red]No TTY and no flags given.[/red] "
                "Use: [bold]sun model --key <KEY> --base-url <URL> --model <NAME>[/bold]"
            )
            raise typer.Exit(1)
        path = write_config(values)
        console.print(f"[green]✓ Saved to[/green] {path}")
        return

    # Interactive path.
    console.print(
        Panel("Configure the model Sun should use.", title="🛠  sun model", border_style="cyan")
    )
    base_url = Prompt.ask(
        "Base URL (OpenAI-compatible endpoint)",
        default=current.get("base_url", "https://api.deepseek.com/v1"),
    )
    model_name = Prompt.ask("Model name", default=current.get("model", "deepseek-v4-flash"))
    has_key = bool(current.get("api_key"))
    key_prompt = "API key" + (" (leave blank to keep existing)" if has_key else "")
    api_key = Prompt.ask(key_prompt, password=True, default="" if has_key else None)

    values = {"base_url": base_url, "model": model_name}
    if api_key:
        values["api_key"] = api_key

    path = write_config(values)
    console.print(f"[green]✓ Saved to[/green] {path}")
    console.print('Test it with:  [bold]sun "say hi"[/bold]')


@app.command()
def config() -> None:
    """Show the current effective configuration (secrets masked)."""
    s = load_settings()
    table = Table(title="Effective configuration", show_header=True)
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    def mask(v: str) -> str:
        if not v:
            return "[red](not set)[/red]"
        return v[:6] + "…" + v[-4:] if len(v) > 12 else "***"

    table.add_row("api_key", mask(s.api_key))
    table.add_row("base_url", s.base_url)
    table.add_row("model", s.model)
    table.add_row("max_turns", str(s.max_turns))
    table.add_row("shell_timeout", str(s.shell_timeout))
    console.print(table)
    console.print(f"[dim]Config file: {global_config_path()}[/dim]")


@app.command()
def update() -> None:
    """Reinstall the latest version from GitHub."""
    console.print("[cyan]Updating Sun from GitHub…[/cyan]")
    if shutil.which("uv") is None:
        console.print("[red]uv not found. Reinstall via the install script.[/red]")
        raise typer.Exit(1)
    cmd = ["uv", "tool", "install", "--force", GITHUB_SPEC]
    result = subprocess.run(cmd)
    if result.returncode == 0:
        console.print("[green]✓ Updated. Run `sun version` to confirm.[/green]")
    else:
        console.print("[red]Update failed. See output above.[/red]")
        raise typer.Exit(result.returncode)


@app.command()
def remove() -> None:
    """Uninstall Sun (removes the CLI; keeps your config file)."""
    if sys.stdin.isatty() and not Confirm.ask("Uninstall sun?", default=False):
        console.print("Cancelled.")
        raise typer.Exit(0)
    if shutil.which("uv") is None:
        console.print("[red]uv not found; remove manually.[/red]")
        raise typer.Exit(1)
    result = subprocess.run(["uv", "tool", "uninstall", "sun-harness"])
    if result.returncode == 0:
        console.print("[green]✓ Sun uninstalled.[/green]")
        console.print(
            f"[dim]Config left intact at {global_config_path()} (delete manually if desired).[/dim]"
        )
    else:
        raise typer.Exit(result.returncode)


@app.command()
def version() -> None:
    """Show the installed version."""
    console.print(f"sun {__version__}")


@app.command(name="help")
def help_cmd(ctx: typer.Context) -> None:
    """Show help (same as --help)."""
    console.print(ctx.parent.get_help() if ctx.parent else ctx.get_help())


@app.callback(
    invoke_without_command=True,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def _main(ctx: typer.Context) -> None:
    """Sun Agent Harness. Run `sun help` for commands."""
    if ctx.invoked_subcommand is not None:
        return
    args = ctx.args
    if not args:
        # bare `sun` → interactive REPL
        _run_task(None)
    else:
        # `sun "some natural language task"` → run it
        _run_task(" ".join(args))


if __name__ == "__main__":
    app()
