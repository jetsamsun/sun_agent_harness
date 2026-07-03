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

### Install (one line)

```bash
curl -fsSL https://raw.githubusercontent.com/jetsamsun/sun_agent_harness/main/install/install.sh | bash
```

This installs `uv` (if missing) and the `sun` CLI globally from GitHub.

<details>
<summary>Or install manually with uv</summary>

```bash
uv tool install git+https://github.com/jetsamsun/sun_agent_harness.git
```
</details>

### Configure

```bash
sun model                     # interactive: set API key / base URL / model
# or non-interactively:
sun model --key <KEY> --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
```

Config is saved to `~/.config/sun/config.toml` and used from any directory.

### Use

```bash
sun "how many .py files are in this directory?"   # one-shot task
sun                            # interactive REPL
sun config                     # show effective config (key masked)
sun help                       # list commands
sun update                     # reinstall latest from GitHub
sun remove                     # uninstall
```

## Commands

| Command | Description |
|---------|-------------|
| `sun "<task>"` | Run a task (natural language) |
| `sun run "<task>"` | Same as above (explicit) |
| `sun model` | Configure LLM (interactive or via `--key/--base-url/--model`) |
| `sun config` | Show effective configuration |
| `sun update` | Reinstall latest version from GitHub |
| `sun remove` | Uninstall |
| `sun version` | Show version |
| `sun help` | List all commands |

## Configuration precedence

Highest to lowest: **environment vars (`SUN_*`)** → **project `.env`** →
**global `~/.config/sun/config.toml`** → defaults. So a project can override
the global model with a local `.env`, and a one-off `SUN_MODEL=... sun ...`
overrides everything.

| Var | Default | Meaning |
|-----|---------|---------|
| `SUN_API_KEY` | — | OpenAI-compatible API key |
| `SUN_BASE_URL` | api.openai.com/v1 | Endpoint |
| `SUN_MODEL` | gpt-4o-mini | Model name |
| `SUN_MAX_TURNS` | 25 | Hard cap on reasoning↔tool iterations |
| `SUN_MAX_RETRIES` | 4 | Retry attempts on transient LLM errors |
| `SUN_SHELL_TIMEOUT` | 60 | Seconds before a shell command is killed |
| `SUN_REQUIRE_CONFIRMATION` | true | Prompt y/n on dangerous ops |

## Development

```bash
git clone https://github.com/jetsamsun/sun_agent_harness.git
cd sun_agent_harness
uv sync
uv run pytest -q                # tests (no API key needed)
uv run sun "your task"          # run from the source tree
```

## Roadmap

See [`docs/roadmap.md`](docs/roadmap.md).

## License

MIT
