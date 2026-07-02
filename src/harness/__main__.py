"""CLI entry point. Renders the agent's progress with rich and wires up the
interactive confirmation prompt for dangerous operations.

Usage:
    sun "your task here"          # one-shot
    sun                            # interactive REPL
"""

from __future__ import annotations

import json
import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from .config import load_settings
from .llm import LLMClient
from .loop import AgentLoop, Event
from .tools import ToolExecutor, registry, set_shell_timeout

app = typer.Typer(add_completion=False, help="Sun Agent Harness")
console = Console()


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
        # Non-interactive stdin (pipe, CI, no TTY): fail safe by declining,
        # rather than blocking forever on a prompt nobody can answer.
        if not sys.stdin.isatty():
            console.print("[yellow]No interactive TTY — declining dangerous operation.[/yellow]")
            return False
        return Confirm.ask("Proceed?", default=False)

    return confirm


def _build_loop() -> AgentLoop:
    settings = load_settings()
    if not settings.api_key:
        console.print("[red]Missing SUN_API_KEY. Set it in .env or environment.[/red]")
        raise typer.Exit(1)
    set_shell_timeout(settings.shell_timeout)

    llm = LLMClient(settings)
    executor = ToolExecutor(registry, settings, confirm_fn=_make_confirm_fn())
    return AgentLoop(llm, registry, executor, settings, on_event=_make_event_printer())


@app.command()
def main(task: str = typer.Argument(None, help="Task for the agent. Omit for REPL.")) -> None:
    loop = _build_loop()

    if task:
        loop.run(task)
        return

    console.print("[bold]Sun Agent Harness[/bold] — interactive mode. Ctrl-C to exit.")
    while True:
        try:
            task = console.input("[bold cyan]sun>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye")
            sys.exit(0)
        if not task:
            continue
        loop.run(task)


if __name__ == "__main__":
    app()
