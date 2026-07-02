# Contributing to Sun Agent Harness

Thanks for your interest! This is a deliberately small, hackable agent
harness. The guiding philosophy: **a thin, fully-controllable kernel** — no
heavy frameworks, every abstraction earns its place.

## Development setup

```bash
git clone <repo-url>
cd sun_agent_harness
uv sync                       # installs deps + the package (editable)
cp .env.example .env          # add your OpenAI-compatible API key
```

Run the agent:

```bash
uv run sun "your task"        # one-shot
uv run sun                    # interactive REPL
```

## Before you open a PR

Run these locally — CI runs the same:

```bash
uv run ruff check src tests   # lint
uv run ruff format src tests  # format
uv run pytest -q              # tests (no API key needed for the wiring suite)
```

## Test layout

- `tests/test_wiring.py` — runs **without** an API key. Covers the tool
  registry, executor, safety gate, and schema generation. **Every PR must
  keep these green.**
- Acceptance cases that call a live model are documented in `docs/roadmap.md`
  and run manually against a real endpoint.

## Adding a tool

Tools live in `src/harness/tools/builtins.py`. Register with the `@registry.tool`
decorator; the JSON schema is derived from type hints + a `:param name:`
docstring convention. Mark destructive tools `dangerous=True` and, for shell,
extend the blocklist in `safety.py` with a regression test.

## Design principles (please respect)

1. **Context engineering > prompt engineering** — truncate/offload large
   tool output before it re-enters the window.
2. **Verifiable stop conditions** — completion is signalled by the `finish`
   tool, never guessed from prose.
3. **No fabricated results** — never invent tool output; surface real errors.
4. **Fail safe** — dangerous ops require confirmation; non-interactive
   environments decline rather than block.

## Scope

Check `docs/roadmap.md` for what's in/out of the current stage before
proposing large features. Stage 1 is intentionally minimal (single agent,
CLI, in-memory context).
