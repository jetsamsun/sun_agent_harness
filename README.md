# Sun Agent Harness

A minimal, self-built **agent harness** for the Linux command line. No heavy
frameworks — a thin, fully-controllable kernel that turns each LLM decision
into a reliable side effect.

> **Stage 1 goal:** a single agent that closes the loop in a shell —
> take a natural-language task, autonomously call tools, observe results,
> and keep going until the task is done or judged impossible.

## Architecture

```
CLI (__main__.py)           ← interactive REPL / one-shot, rich output
  └─ AgentLoop (loop.py)    ← the heart: reason ↔ tool cycle, stop logic
       ├─ LLMClient (llm.py)          ← OpenAI-compatible, native function calling
       ├─ Context (context.py)        ← in-memory message stack + token estimate
       └─ Tools
            ├─ ToolRegistry           ← @tool decorator → JSON schema
            ├─ ToolExecutor           ← validate · safety-gate · run · truncate
            └─ built-ins: run_shell, read_file, write_file, finish
                 └─ Safety (safety.py) ← dangerous-command blocklist + confirm
```

### Design principles
1. **Context engineering > prompt engineering.** Large tool output is
   truncated before it re-enters the window.
2. **Verifiable stop condition.** The model declares completion via the
   `finish` tool, not by us guessing from prose.
3. **No fabricated results.** The system prompt forbids inventing output;
   safety gates real side effects.

## Quick start

```bash
uv sync                       # install
cp .env.example .env          # add your API key
uv run sun "how many .py files are in this directory?"   # one-shot
uv run sun                    # interactive REPL
```

## Configuration

All settings are env vars prefixed `SUN_` (see `.env.example`).

| Var | Default | Meaning |
|-----|---------|---------|
| `SUN_API_KEY` | — | OpenAI-compatible API key |
| `SUN_BASE_URL` | api.openai.com/v1 | Endpoint |
| `SUN_MODEL` | gpt-4o-mini | Model name |
| `SUN_MAX_TURNS` | 25 | Hard cap on reasoning↔tool iterations |
| `SUN_SHELL_TIMEOUT` | 60 | Seconds before a shell command is killed |
| `SUN_REQUIRE_CONFIRMATION` | true | Prompt y/n on dangerous ops |

## Roadmap

See [`docs/roadmap.md`](docs/roadmap.md).

## License

MIT
