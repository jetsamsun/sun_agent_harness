"""CLI entry point.

Commands:
    sun "task"            run a task (shorthand for `sun run`)
    sun run "task"        run a task; omit task for interactive REPL
    sun model             configure the LLM (api key / base url / model)
    sun persona           show / edit PERSONA.md (seeds SQLite once)
    sun memory            SQLite 长久记忆（人格/规则/背景；删除需确认）
    sun sessions          list / prune Redis 聊天会话（可随时清）
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
from .long_memory import KINDS, LongMemoryError, open_long_memory
from .loop import AgentLoop, Event
from .persona import (
    ensure_persona_file,
    load_persona_text,
    open_persona_in_editor,
    resolve_persona_path,
)
from .repl_input import read_repl_message
from .session_store import SessionStore, SessionStoreError
from .tools import (
    ToolExecutor,
    fetch_model_status,
    registry,
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
from .trace import TraceSink, default_trace_path
from .workspace import set_workspace_root

GITHUB_SPEC = "git+https://github.com/jetsamsun/sun_agent_harness.git"


app = typer.Typer(
    add_completion=False,
    help="Sun Agent Harness — a minimal agent for your terminal.",
    no_args_is_help=False,
)
console = Console(safe_box=True)


def _configure_stdio() -> None:
    """Avoid GBK UnicodeEncodeError on Windows consoles (emoji / CJK mix)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _cprint(*args: object, **kwargs: object) -> None:
    """console.print that never crashes the agent on encoding errors."""
    try:
        console.print(*args, **kwargs)
    except UnicodeEncodeError:
        plain = []
        for a in args:
            s = str(a)
            plain.append(s.encode("ascii", errors="replace").decode("ascii"))
        try:
            console.print(*plain, **kwargs)
        except Exception:  # noqa: BLE001
            sys.stdout.write(" ".join(plain) + "\n")


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _make_event_printer(*, show_usage: bool = True):
    # Buffer streamed text: flush as think only before a tool call.
    # Final answers go solely into the green Done panel (no double print).
    stream_buf: list[str] = []

    def _flush_stream_as_think() -> None:
        if not stream_buf:
            return
        text = "".join(stream_buf).strip()
        stream_buf.clear()
        if text:
            _cprint(f"[dim]think: {text}[/dim]")

    def printer(event: Event) -> None:
        if event.kind == "think_delta":
            stream_buf.append(event.data["text"])
            return

        if event.kind == "env":
            env = event.data.get("env") or {}
            _cprint(
                f"[dim]env: {env.get('family', '?')} · "
                f"cwd={env.get('cwd', '?')}[/dim]"
            )
        elif event.kind == "compress":
            _cprint(
                f"[dim]compress: {event.data.get('method')} · "
                f"{event.data.get('before_tokens')}->{event.data.get('after_tokens')} tok · "
                f"drop {event.data.get('dropped_messages')} msgs[/dim]"
            )
        elif event.kind == "think":
            if event.data.get("streamed"):
                # Final streamed answer → Done panel will show it; drop buffer.
                stream_buf.clear()
                return
            _flush_stream_as_think()
            _cprint(f"[dim]think: {event.data['text']}[/dim]")
        elif event.kind == "tool_call":
            _flush_stream_as_think()
            args = event.data["args"]
            try:
                args = json.dumps(json.loads(args), ensure_ascii=False)
            except Exception:  # noqa: BLE001
                pass
            _cprint(f"[cyan]-> {event.data['name']}[/cyan] [dim]{args}[/dim]")
        elif event.kind == "tool_result":
            result = event.data["result"]
            ok = result.get("success")
            marker = "[green]OK[/green]" if ok else "[red]ERR[/red]"
            preview = (
                result.get("stdout")
                or result.get("content")
                or result.get("error")
                or result.get("summary")
                or ""
            )
            preview = str(preview).strip()
            if len(preview) > 500:
                preview = preview[:500] + " ..."
            ms = event.data.get("latency_ms")
            timing = f" [dim]({ms}ms)[/dim]" if ms is not None else ""
            _cprint(f"  {marker} [dim]{preview}[/dim]{timing}")
        elif event.kind == "finish":
            stream_buf.clear()
            _cprint(
                Panel(event.data["summary"], title="Done", border_style="green")
            )
            if show_usage and event.data.get("usage"):
                _print_usage(event.data["usage"])
        elif event.kind == "stop":
            stream_buf.clear()
            _cprint(
                Panel(
                    f"Stopped: {event.data['reason']}",
                    title="Stopped",
                    border_style="yellow",
                )
            )
            if show_usage and event.data.get("usage"):
                _print_usage(event.data["usage"])
        elif event.kind == "usage" and show_usage:
            # finish/stop already printed; skip duplicate unless orphaned
            pass
        elif event.kind == "session":
            action = event.data.get("action")
            sid = event.data.get("id", "?")
            if action == "new":
                _cprint(f"[dim]session {sid} (redis)[/dim]")
            elif action == "save":
                _cprint(
                    f"[dim]saved {sid} · turns={event.data.get('user_turns')} "
                    f"· {event.data.get('status')}[/dim]"
                )
            elif action == "resume":
                title = event.data.get("title") or ""
                extra = f" · {title}" if title else ""
                _cprint(f"[dim]resumed {sid}{extra}[/dim]")

    return printer


def _print_usage(usage: dict) -> None:
    from .usage import UsageTotals

    totals = UsageTotals(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cache_hit_tokens=int(usage.get("cache_hit_tokens") or 0),
        cache_miss_tokens=int(usage.get("cache_miss_tokens") or 0),
        llm_calls=int(usage.get("llm_calls") or 0),
        tool_calls=int(usage.get("tool_calls") or 0),
        llm_ms=float(usage.get("llm_ms") or 0),
        tool_ms=float(usage.get("tool_ms") or 0),
        model=str(usage.get("model") or ""),
        cost_cny=float(usage.get("est_cost_cny") or 0),
    )
    wall = usage.get("wall_ms")
    extra = f" · wall {wall/1000:.1f}s" if wall is not None else ""
    _cprint(f"[dim]time {totals.summary_line()}{extra}[/dim]")


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


def _connect_store(settings) -> SessionStore | None:
    """Connect Redis when SUN_REDIS_URL is set; hard-fail if unreachable."""
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        return SessionStore.connect(url, prefix=settings.redis_prefix or "sun")
    except SessionStoreError as exc:
        console.print(f"[red]Redis required but unavailable:[/red] {exc}")
        raise typer.Exit(1) from exc


def _build_loop() -> tuple[AgentLoop, TraceSink | None]:
    _configure_stdio()
    settings = load_settings()
    if not settings.api_key:
        console.print("[red]No API key configured.[/red] Run [bold]sun model[/bold] to set it up.")
        raise typer.Exit(1)
    root = settings.workspace_root.strip() or str(Path.cwd())
    set_workspace_root(root)
    set_shell_timeout(settings.shell_timeout)
    set_confirm_edits(settings.confirm_edits)
    set_llm_config(
        api_key=settings.api_key,
        base_url=settings.base_url,
        model=settings.model,
    )
    set_sqlite_path(settings.sqlite_path)
    set_secret_vault_config(
        url=settings.secret_vault_url,
        token=settings.secret_vault_token,
    )
    set_auto_git_checkpoint(settings.auto_git_checkpoint)
    set_ask_fn(_make_ask_fn())
    set_plan_confirm_fn(_make_plan_confirm_fn())
    set_edit_confirm_fn(_make_edit_confirm_fn())
    store = _connect_store(settings)
    set_session_store(store)
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
    return (
        AgentLoop(llm, registry, executor, settings, on_event=on_event, store=store),
        sink,
    )


_REPL_EXIT = {"exit", "quit", "/exit", "/quit", "bye", "q"}
_REPL_EXIT_COMPACT = {
    "exit",
    "quit",
    "/exit",
    "/quit",
    "bye",
    "q",
    "退出",
    "退出sun",
    "再见",
    "拜拜",
}
_REPL_CLEAR = {"clear", "/clear", "/new", "new"}
_REPL_TOKENS = {"tokens", "/tokens"}


def _is_repl_exit(line: str) -> bool:
    """True for exit / quit / 退出 / 退出 sun / 再见, etc."""
    raw = line.strip()
    if not raw or "\n" in raw:
        return False
    low = raw.lower()
    if low in _REPL_EXIT:
        return True
    compact = "".join(raw.split()).lower()
    return compact in _REPL_EXIT_COMPACT


def _print_sessions_table(rows: list) -> None:
    if not rows:
        console.print("[dim]No sessions for this cwd.[/dim]")
        return
    table = Table(title="Sessions (this cwd)", show_header=True)
    table.add_column("id", style="cyan")
    table.add_column("updated")
    table.add_column("turns")
    table.add_column("status")
    table.add_column("title")
    for m in rows:
        table.add_row(
            m.id,
            (m.updated_at or "")[:19],
            str(m.user_turns),
            m.status,
            m.title or "",
        )
    console.print(table)


def _repl_handle_line(loop: AgentLoop, line: str) -> bool:
    """Handle REPL meta-commands. Returns True if the line was consumed."""
    raw = line.strip()
    low = raw.lower()
    parts = raw.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if _is_repl_exit(raw):
        console.print("bye")
        raise typer.Exit(0)

    if low in _REPL_CLEAR:
        loop.clear_session()
        console.print("[dim]New session (in-memory cleared; next message starts fresh).[/dim]")
        return True

    if low in _REPL_TOKENS:
        ctx = loop.session_context()
        usage = loop.session_usage()
        sid = loop.session_id()
        sid_bit = f" · id {sid}" if sid else ""
        if ctx is None:
            console.print(f"[dim]No session yet (0 tokens){sid_bit}[/dim]")
        else:
            n = ctx.token_estimate()
            turns = ctx.user_turns()
            label = f"~{n} ctx tokens" if n >= 0 else "ctx tokens unknown"
            console.print(
                f"[dim]{label} · {turns} user turn(s) · "
                f"api {usage.total_tokens} tok{sid_bit}[/dim]"
            )
        return True

    if cmd in {"/sessions", "sessions"}:
        try:
            if arg.lower() == "prune":
                if not sys.stdin.isatty() or not Confirm.ask(
                    "Delete all Redis sessions for this cwd?", default=False
                ):
                    console.print("[dim]Cancelled.[/dim]")
                    return True
                deleted = loop.prune_sessions()
                console.print(f"[green]Pruned {len(deleted)} session(s).[/green]")
                return True
            _print_sessions_table(loop.list_sessions())
        except SessionStoreError as exc:
            console.print(f"[red]{exc}[/red]")
        return True

    if cmd in {"/resume", "resume"}:
        if not arg:
            console.print("[yellow]Usage: /resume <session_id>[/yellow]")
            return True
        try:
            sid = loop.resume_session(arg)
            console.print(f"[green]Resumed[/green] {sid}")
        except SessionStoreError as exc:
            console.print(f"[red]{exc}[/red]")
        return True

    if cmd in {"/memory", "memory"}:
        settings = load_settings()
        mem = open_long_memory(settings.sqlite_path)
        try:
            if not arg or arg.lower() in {"list", "dump", "all"}:
                _print_memory_dump(mem)
            elif arg.lower() == "table":
                _print_memory_table(mem.list())
                console.print(f"[dim]db → {mem.path}[/dim]")
            else:
                console.print(
                    "[dim]REPL: /memory 全文；/memory table 表格。"
                    " 写入/删除请用[/dim] [bold]sun memory set|delete[/bold]"
                )
        finally:
            mem.close()
        return True

    if cmd in {"/models", "models"}:
        settings = load_settings()
        set_llm_config(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
        )
        _print_models_status(fetch_model_status())
        return True

    return False


def _print_models_status(info: dict) -> None:
    current = str(info.get("current_model") or "")
    base = str(info.get("base_url") or "")
    console.print(f"[bold]当前模型[/bold] {current or '(未配置)'}")
    console.print(f"[dim]endpoint[/dim] {base or '(未配置)'}")
    if not info.get("success"):
        console.print(f"[red]{info.get('error') or 'list models failed'}[/red]")
        return
    rows = info.get("available_models") or []
    if not rows:
        console.print("[dim]端点未返回可用模型列表。[/dim]")
        return
    table = Table(title=f"可用模型（{info.get('count', len(rows))}）", show_header=True)
    table.add_column("model", style="cyan")
    table.add_column("owned_by")
    table.add_column("current")
    for m in rows:
        table.add_row(
            str(m.get("id") or ""),
            str(m.get("owned_by") or ""),
            "yes" if m.get("current") else "",
        )
    console.print(table)
    hint = info.get("switch_hint")
    if hint:
        console.print(f"[dim]{hint}[/dim]")


def _print_memory_table(rows: list) -> None:
    """Legacy table view (kept for optional use)."""
    if not rows:
        console.print("[dim]长久记忆为空（SQLite）。用 sun memory set 添加。[/dim]")
        return
    table = Table(title="长久记忆 · SQLite（不会被 sessions prune 清除）", show_header=True)
    table.add_column("id", style="cyan")
    table.add_column("kind")
    table.add_column("key")
    table.add_column("title")
    table.add_column("updated")
    for e in rows:
        table.add_row(
            str(e.id),
            e.kind,
            e.key,
            e.title or "",
            (e.updated_at or "")[:19],
        )
    console.print(table)


def _print_memory_dump(mem) -> None:
    """Print full memory in the fixed 【】 / [] section layout."""
    text = mem.format_dump()
    console.print(text)
    console.print(f"\n[dim]db → {mem.path}[/dim]")


def _read_sun_prompt() -> str:
    """One REPL turn; drains multi-line pastes into a single message."""

    def prompt_line() -> str:
        return console.input("[bold cyan]sun>[/bold cyan] ")

    def cont_line() -> str:
        return console.input("[bold cyan]... [/bold cyan] ")

    return read_repl_message(prompt_line=prompt_line, cont_prompt_line=cont_line)


def _run_task(task: str | None) -> None:
    loop, sink = _build_loop()
    try:
        if task:
            # One-shot: no session memory across process invocations.
            loop.run(task, session=False)
            return
        redis_hint = (
            "[dim]/new · /sessions · /resume <id> · /memory · /models · tokens · exit[/dim]"
            if loop.has_store()
            else "[dim]/new · /memory · /models · tokens · exit[/dim] "
            "[dim yellow](set SUN_REDIS_URL for /resume)[/dim yellow]"
        )
        console.print(f"[bold]Sun[/bold] — interactive mode. {redis_hint}")
        console.print(
            "[dim]多行：直接粘贴即可；或 /paste 后粘贴，单独一行 --- 结束。"
            " 行末 \\ 也可续行。[/dim]"
        )
        while True:
            try:
                line = _read_sun_prompt()
            except (EOFError, KeyboardInterrupt):
                console.print("\nbye")
                raise typer.Exit(0) from None
            if not line:
                continue
            # Meta-commands only when the whole turn is a single-line command.
            if "\n" not in line and _repl_handle_line(loop, line):
                continue
            try:
                loop.run(line, session=True)
            except SessionStoreError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1) from exc
            if loop.should_quit_repl():
                console.print("bye")
                raise typer.Exit(0)
            ctx = loop.session_context()
            if ctx is not None:
                n = ctx.token_estimate()
                sid = loop.session_id()
                sid_bit = f" · {sid}" if sid else ""
                if n >= 0:
                    console.print(f"[dim]session ~{n} ctx tokens{sid_bit}[/dim]")
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
def persona(
    edit: bool = typer.Option(False, "--edit", "-e", help="Open PERSONA.md in an editor."),
    init: bool = typer.Option(
        False, "--init", help="Create a project-local .sun/PERSONA.md in cwd."
    ),
) -> None:
    """Show or edit PERSONA.md (used to seed SQLite persona once).

    Durable edits: prefer `sun memory set --kind persona`.
    """
    settings = load_settings()
    if init:
        path = Path.cwd() / ".sun" / "PERSONA.md"
        created = ensure_persona_file(path)
        console.print(
            f"[green]✓ {'Created' if created else 'Already exists'}[/green] {path}"
        )
    else:
        path = resolve_persona_path(settings.persona_path)
        ensure_persona_file(path)

    if edit:
        console.print(f"[cyan]Opening[/cyan] {path}")
        open_persona_in_editor(path)
        return

    text = load_persona_text(path)
    preview = text if len(text) <= 4000 else text[:4000] + "\n…"
    console.print(
        Panel(
            preview or "[dim](empty)[/dim]",
            title=f"PERSONA.md · {path}",
            border_style="cyan",
        )
    )
    console.print(
        "[dim]File seed only. Durable 人格/规则/背景 →[/dim] "
        "[bold]sun memory[/bold]"
    )


@app.command()
def memory(
    action: str = typer.Argument(
        "list",
        help="list | show | set | delete",
    ),
    target: str = typer.Argument(
        "",
        help="For show/delete: entry id. For set: unused.",
    ),
    kind: str = typer.Option(
        "other",
        "--kind",
        "-k",
        help=f"Entry kind: {', '.join(KINDS)}（系统提示词/铁律/开发环境/人格/项目背景/其他）",
    ),
    key: str = typer.Option(
        "default",
        "--key",
        help="Ignored (compat). Each kind has exactly one entry.",
    ),
    title: str = typer.Option("", "--title", "-t", help="Short title."),
    append: bool = typer.Option(
        False,
        "--append",
        help="Append to existing content for this kind instead of replace.",
    ),
    file: str = typer.Option(
        "", "--file", "-f", help="Read content from a text file."
    ),
    content: str = typer.Option("", "--content", "-c", help="Inline content."),
) -> None:
    """Manage SQLite durable memory (system/iron/dev_env/persona/project/other).

    Not cleared by sun sessions --prune. Deletes always require confirmation.
    """
    settings = load_settings()
    mem = open_long_memory(settings.sqlite_path)
    act = action.strip().lower()
    try:
        if act == "list":
            if (target or "").strip().lower() in {"table", "--table"}:
                _print_memory_table(mem.list())
                console.print(f"[dim]db → {mem.path}[/dim]")
            else:
                _print_memory_dump(mem)
            return

        if act == "show":
            if not target.isdigit():
                console.print("[red]Usage: sun memory show <id>[/red]")
                raise typer.Exit(1)
            entry = mem.get(int(target))
            if entry is None:
                console.print(f"[red]No entry id={target}[/red]")
                raise typer.Exit(1)
            console.print(
                Panel(
                    entry.content,
                    title=f"#{entry.id} {entry.kind}/{entry.key} · {entry.title}",
                    border_style="cyan",
                )
            )
            return

        if act == "set":
            body = content.strip()
            if file.strip():
                body = Path(file).read_text(encoding="utf-8").strip()
            if not body:
                if not sys.stdin.isatty():
                    console.print("[red]Provide --content or --file[/red]")
                    raise typer.Exit(1)
                body = Prompt.ask("Content (one line or paste)").strip()
            if not body:
                console.print("[red]Empty content[/red]")
                raise typer.Exit(1)
            try:
                entry = mem.upsert(
                    kind=kind,
                    key=key,
                    content=body,
                    title=title,
                    append=append,
                )
            except LongMemoryError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1) from exc
            console.print(
                f"[green]✓ Saved[/green] #{entry.id} {entry.kind}/{entry.key} → {mem.path}"
            )
            return

        if act == "delete":
            if not target.isdigit():
                console.print("[red]Usage: sun memory delete <id>[/red]")
                raise typer.Exit(1)
            eid = int(target)
            entry = mem.get(eid)
            if entry is None:
                console.print(f"[red]No entry id={eid}[/red]")
                raise typer.Exit(1)
            console.print(
                Panel(
                    f"{entry.kind}/{entry.key}\n{entry.title}\n\n{entry.content[:500]}",
                    title=f"⚠ Delete durable memory #{eid}",
                    border_style="red",
                )
            )
            if not sys.stdin.isatty() or not Confirm.ask(
                "Permanently delete this SQLite entry? (not undoable)", default=False
            ):
                console.print("[dim]Cancelled.[/dim]")
                raise typer.Exit(0)
            mem.delete(eid)
            console.print(f"[green]Deleted[/green] #{eid}")
            return

        console.print("[red]Unknown action. Use list|show|set|delete[/red]")
        raise typer.Exit(1)
    finally:
        mem.close()


@app.command()
def sessions(
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Delete Redis chat sessions for this cwd (does NOT touch SQLite).",
    ),
) -> None:
    """List (or prune) Redis chat sessions for the current directory.

    Requires SUN_REDIS_URL. Chat memory only — SQLite durable memory is untouched.
    """
    settings = load_settings()
    store = _connect_store(settings)
    if store is None:
        console.print(
            "[red]SUN_REDIS_URL not set.[/red] "
            "Add e.g. [bold]SUN_REDIS_URL=redis://127.0.0.1:6379/0[/bold] to .env"
        )
        raise typer.Exit(1)
    cwd = str(Path.cwd())
    if prune:
        if sys.stdin.isatty() and not Confirm.ask(
            f"Delete all sessions for cwd {cwd}?", default=False
        ):
            console.print("Cancelled.")
            raise typer.Exit(0)
        deleted = store.prune(cwd=cwd)
        console.print(f"[green]Pruned {len(deleted)} session(s).[/green]")
        return
    _print_sessions_table(store.list_sessions(cwd=cwd))


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
    table.add_row(
        "redis_url",
        s.redis_url.strip() or "[dim](off — no persistence)[/dim]",
    )
    table.add_row("redis_prefix", s.redis_prefix or "sun")
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
    table.add_row(
        "persona_path",
        s.persona_path.strip()
        or str(resolve_persona_path(""))
        + " [dim](auto)[/dim]",
    )
    from .long_memory import resolve_sqlite_path

    table.add_row(
        "sqlite_path",
        s.sqlite_path.strip()
        or str(resolve_sqlite_path("")) + " [dim](auto)[/dim]",
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
_KNOWN_COMMANDS = {
    "run",
    "model",
    "persona",
    "memory",
    "sessions",
    "config",
    "update",
    "remove",
    "version",
    "help",
}


def main() -> None:
    """Console-script entry point.

    Pre-processes argv so `sun "some task"` works: if the first non-flag
    argument isn't a known subcommand, inject `run` before it. This is more
    robust across install methods than hacking click's command resolution.
    """
    _configure_stdio()
    argv = sys.argv[1:]
    if argv:
        first = argv[0]
        # Leave --help/-h and known commands alone; everything else → run.
        if not first.startswith("-") and first not in _KNOWN_COMMANDS:
            sys.argv = [sys.argv[0], "run", *argv]
    app()


if __name__ == "__main__":
    main()
