#!/usr/bin/env bash
#
# Sun Agent Harness — one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/jetsamsun/sun_agent_harness/main/install/install.sh | bash
#
# What it does:
#   1. Ensures `uv` (the Python package manager) is installed.
#   2. Installs the `sun` CLI globally from GitHub via `uv tool install`.
#   3. Makes sure ~/.local/bin is on your PATH.
#   4. Points you at `sun model` to configure your LLM.
#
set -euo pipefail

REPO="git+https://github.com/jetsamsun/sun_agent_harness.git"
BIN_DIR="${HOME}/.local/bin"

info()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()    { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m!\033[0m %s\n' "$*"; }
die()   { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. Ensure uv -----------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  ok "uv already installed ($(uv --version))"
else
  info "Installing uv (Python package manager)…"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    die "Neither curl nor wget found. Install one and re-run."
  fi
  # uv installs into ~/.local/bin or ~/.cargo/bin — source its env if present.
  [ -f "${HOME}/.local/bin/env" ] && . "${HOME}/.local/bin/env" || true
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v uv >/dev/null 2>&1 || die "uv install failed; open a new shell and re-run."
  ok "uv installed"
fi

# --- 2. Install sun ---------------------------------------------------------
info "Installing sun from GitHub…"
uv tool install --force "${REPO}" || die "sun install failed (network to GitHub?)."
ok "sun installed"

# --- 3. Ensure PATH ---------------------------------------------------------
if ! command -v sun >/dev/null 2>&1; then
  info "Adding ${BIN_DIR} to PATH…"
  uv tool update-shell 2>/dev/null || true
  case ":${PATH}:" in
    *":${BIN_DIR}:"*) : ;;
    *)
      for rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile"; do
        [ -f "${rc}" ] || continue
        if ! grep -q "${BIN_DIR}" "${rc}" 2>/dev/null; then
          printf '\nexport PATH="%s:$PATH"\n' "${BIN_DIR}" >> "${rc}"
        fi
      done
      ;;
  esac
  warn "Open a NEW terminal (or run: export PATH=\"${BIN_DIR}:\$PATH\") so \`sun\` is found."
fi

# --- 4. Done ----------------------------------------------------------------
echo
ok "Sun Agent Harness is installed."
echo
echo "Next steps:"
echo "  1. Configure your model:   sun model"
echo "  2. Run a task:             sun \"list the biggest files here\""
echo "  3. See all commands:       sun --help"
echo
