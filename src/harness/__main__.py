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
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__
from .config import global_config_path, load_settings
from .config_writer import read_config, write_config
from .gitops import set_auto_git_checkpoint
from .llm import LLMClient
from .loop import AgentLoop, Event
from .tools import (
    ToolExecutor,
    registry,
    set_ask_fn,
    set_confirm_edits,
    set_edit_confirm_fn,
    set_plan_confirm_fn,
    set_shell_timeout,
)
from .trace import TraceSink, default_trace_path
from .workspace import set_workspace_root

GITHUB_SPEC = "git+https://github.com/jetsamsun/sun_agent_harness.git"


app = typer.Typer(
    add_completion=False,
    help="Sun Agent Harness — a minimal agent for your terminal.",
    no_args_is_help=False,
)
console = Console()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _make_event_printer(*, show_usage: bool = True):
    # Buffer streamed text: flush as 💭 only before a tool call.
    # Final answers go solely into the green Done panel (no double print).
    stream_buf: list[str] = []

    def _flush_stream_as_think() -> None:
        if not stream_buf:
            return
        text = "".join(stream_buf).strip()
        stream_buf.clear()
        if text:
            console.print(f"[dim]💭 {text}[/dim]")

    def printer(event: Event) -> None:
        if event.kind == "think_delta":
            stream_buf.append(event.data["text"])
            return

        if event.kind == "env":
            env = event.data.get("env") or {}
            console.print(
                f"[dim]🖥 环境: {env.get('family', '?')} · "
                f"cwd={env.get('cwd', '?')}[/dim]"
            )
        elif event.kind == "compress":
            console.print(
                f"[dim]🗜 上下文压缩 · {event.data.get('method')} · "
                f"{event.data.get('before_tokens')}→{event.data.get('after_tokens')} tok · "
                f"drop {event.data.get('dropped_messages')} msgs[/dim]"
            )
        elif event.kind == "think":
            if event.data.get("streamed"):
                # Final streamed answer → Done panel will show it; drop buffer.
                stream_buf.clear()
                return
            _flush_stream_as_think()
            console.print(f"[dim]💭 {event.data['text']}[/dim]")
        elif event.kind == "tool_call":
            _flush_stream_as_think()
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
            ms = event.data.get("latency_ms")
            timing = f" [dim]({ms}ms)[/dim]" if ms is not None else ""
            console.print(f"  {marker} [dim]{preview}[/dim]{timing}")
        elif event.kind == "finish":
            stream_buf.clear()
            console.print(
                Panel(event.data["summary"], title="✅ Done", border_style="green")
            )
            if show_usage and event.data.get("usage"):
                _print_usage(event.data["usage"])
        elif event.kind == "stop":
            stream_buf.clear()
            console.print(
                Panel(
                    f"Stopped: {event.data['reason']}",
                    title="⏹ Stopped",
                    border_style="yellow",
                )
            )
            if show_usage and event.data.get("usage"):
                _print_usage(event.data["usage"])
        elif event.kind == "usage" and show_usage:
            # finish/stop already printed; skip duplicate unless orphaned
            pass

    return printer


def _print_usage(usage: dict) -> None:
    from .usage import UsageTotals

    totals = UsageTotals(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        llm_calls=int(usage.get("llm_calls") or 0),
        tool_calls=int(usage.get("tool_calls") or 0),
        llm_ms=float(usage.get("llm_ms") or 0),
        tool_ms=float(usage.get("tool_ms") or 0),
        model=str(usage.get("model") or ""),
    )
    wall = usage.get("wall_ms")
    extra = f" · wall {wall/1000:.1f}s" if wall is not None else ""
    console.print(f"[dim]⏱ {totals.summary_line()}{extra}[/dim]")


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


def _make_ask_fn():
    def ask(question: str) -> str:
        console.print(Panel(question, title="❓ Sun asks", border_style="cyan"))
        return Prompt.ask("Your answer").strip()

    return ask


def _make_plan_confirm_fn():
    def confirm_plan(title: str, steps: list) -> tuple[bool, str]:
        lines = [f"[bold]{title}[/bold]", ""]
        for step in steps:
            lines.append(f"[cyan]{step['id']}[/cyan]. {step['title']}")
            lines.append(f"   acceptance: {step['acceptance']}")
        console.print(Panel("\n".join(lines), title="📋 Proposed plan", border_style="magenta"))
        if not sys.stdin.isatty():
            console.print("[yellow]No interactive TTY — plan not approved.[/yellow]")
            return False, "no TTY"
        ok = Confirm.ask("Approve this plan?", default=False)
        if ok:
            return True, ""
        note = Prompt.ask("What should change?", default="").strip()
        return False, note or "rejected"

    return confirm_plan


def _make_edit_confirm_fn():
    def confirm_edit(path: str, diff: str) -> bool:
        preview = diff if len(diff) <= 4000 else diff[:4000] + "\n…"
        console.print(Panel(preview, title=f"📝 Edit {path}", border_style="yellow"))
        if not sys.stdin.isatty():
            console.print("[yellow]No interactive TTY — declining edit.[/yellow]")
            return False
        return Confirm.ask("Apply this edit?", default=True)

    return confirm_edit


def _build_loop() -> tuple[AgentLoop, TraceSink | None]:
    settings = load_settings()
    if not settings.api_key:
        console.print("[red]No API key configured.[/red] Run [bold]sun model[/bold] to set it up.")
        raise typer.Exit(1)
    root = settings.workspace_root.strip() or str(Path.cwd())
    set_workspace_root(root)
    set_shell_timeout(settings.shell_timeout)
    set_confirm_edits(settings.confirm_edits)
    set_auto_git_checkpoint(settings.auto_git_checkpoint)
    set_ask_fn(_make_ask_fn())
    set_plan_confirm_fn(_make_plan_confirm_fn())
    set_edit_confirm_fn(_make_edit_confirm_fn())
    llm = LLMClient(settings)
    executor = ToolExecutor(registry, settings, confirm_fn=_make_confirm_fn())
    printer = _make_event_printer(show_usage=settings.show_usage)
    sink: TraceSink | None = None
    if settings.enable_trace:
        path = settings.trace_log.strip() or str(default_trace_path())
        sink = TraceSink(path, on_event=printer)
        console.print(f"[dim]trace → {path}[/dim]")
        on_event = sink
    else:
        on_event = printer
    return AgentLoop(llm, registry, executor, settings, on_event=on_event), sink


_REPL_EXIT = {"exit", "quit", "/exit", "/quit"}
_REPL_CLEAR = {"clear", "/clear"}
_REPL_TOKENS = {"tokens", "/tokens"}


def _run_task(task: str | None) -> None:
    loop, sink = _build_loop()
    try:
        if task:
            # One-shot: no session memory across process invocations.
            loop.run(task, session=False)
            return
        console.print(
            "[bold]Sun[/bold] — interactive mode (session memory on). "
            "[dim]clear · tokens · exit[/dim]"
        )
        while True:
            try:
                line = console.input("[bold cyan]sun>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\nbye")
                raise typer.Exit(0) from None
            if not line:
                continue
            low = line.lower()
            if low in _REPL_EXIT:
                console.print("bye")
                raise typer.Exit(0)
            if low in _REPL_CLEAR:
                loop.clear_session()
                console.print("[dim]Session cleared.[/dim]")
                continue
            if low in _REPL_TOKENS:
                ctx = loop.session_context()
                usage = loop.session_usage()
                if ctx is None:
                    console.print("[dim]No session yet (0 tokens).[/dim]")
                else:
                    n = ctx.token_estimate()
                    turns = ctx.user_turns()
                    label = f"~{n} ctx tokens" if n >= 0 else "ctx tokens unknown"
                    console.print(
                        f"[dim]{label} · {turns} user turn(s) · "
                        f"api {usage.total_tokens} tok[/dim]"
                    )
                continue
            loop.run(line, session=True)
            ctx = loop.session_context()
            if ctx is not None:
                n = ctx.token_estimate()
                if n >= 0:
                    console.print(f"[dim]session ~{n} ctx tokens[/dim]")
    finally:
        if sink is not None:
            sink.close()


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
    table.add_row(
        "reasoning_effort",
        s.reasoning_effort or "[dim](auto)[/dim]",
    )
    table.add_row("shell_timeout", str(s.shell_timeout))
    table.add_row("confirm_edits", str(s.confirm_edits))
    table.add_row("auto_git_checkpoint", str(s.auto_git_checkpoint))
    table.add_row(
        "workspace_root",
        s.workspace_root.strip() or "[dim](cwd)[/dim]",
    )
    table.add_row("streaming", str(s.streaming))
    table.add_row("show_usage", str(s.show_usage))
    table.add_row("enable_trace", str(s.enable_trace))
    table.add_row(
        "trace_log",
        s.trace_log.strip() or "[dim](.sun/traces/<ts>.jsonl)[/dim]",
    )
    table.add_row("context_compress", str(s.context_compress))
    table.add_row("context_max_tokens", str(s.context_max_tokens))
    table.add_row("context_keep_recent", str(s.context_keep_recent))
    table.add_row("context_compress_llm", str(s.context_compress_llm))
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


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    """Sun Agent Harness. Run `sun help` for commands."""
    # Bare `sun` with no subcommand → interactive REPL.
    if ctx.invoked_subcommand is None:
        _run_task(None)


# Known subcommand names — anything else as the first arg is treated as a
# free-form task and routed to `run`.
_KNOWN_COMMANDS = {"run", "model", "config", "update", "remove", "version", "help"}


def main() -> None:
    """Console-script entry point.

    Pre-processes argv so `sun "some task"` works: if the first non-flag
    argument isn't a known subcommand, inject `run` before it. This is more
    robust across install methods than hacking click's command resolution.
    """
    argv = sys.argv[1:]
    if argv:
        first = argv[0]
        # Leave --help/-h and known commands alone; everything else → run.
        if not first.startswith("-") and first not in _KNOWN_COMMANDS:
            sys.argv = [sys.argv[0], "run", *argv]
    app()


if __name__ == "__main__":
    main()
