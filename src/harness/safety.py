"""Safety layer: detect dangerous shell commands and gate them behind
an interactive confirmation.

Stage 1 uses a simple regex blocklist. It is intentionally conservative —
false positives (asking for confirmation unnecessarily) are acceptable;
false negatives (running something destructive silently) are not.
"""

from __future__ import annotations

import re

# Patterns that should trigger a confirmation prompt before running.
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf]", "recursive/forced delete (rm -rf)"),
    (r"\bmkfs\b", "filesystem format (mkfs)"),
    (r"\bdd\b\s+.*of=", "raw disk write (dd)"),
    # Write-redirect (> or >>) into a system path. We deliberately do NOT match
    # stderr redirects like `2>/dev/null` (a `2` immediately before `>` is a fd
    # spec, not a write to a file), nor the harmless `/dev/null` sink.
    (
        r"(?<![0-9&])>>?\s*/(etc|bin|boot|sys|usr|lib|dev/(?!null\b)\S)",
        "write-redirect into a system path",
    ),
    (r"\bchmod\s+-R\b", "recursive permission change"),
    (r"\bchown\s+-R\b", "recursive ownership change"),
    (r"\b:\(\)\s*\{.*\|.*&\s*\}", "fork bomb"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b", "power state change"),
    (r"\bgit\s+push\b.*--force", "force push"),
    (r"\bsudo\b", "privilege escalation (sudo)"),
]

_COMPILED = [(re.compile(p), reason) for p, reason in _DANGEROUS_PATTERNS]


def assess_command(command: str) -> str | None:
    """Return a human-readable reason if the command looks dangerous, else None."""
    for pattern, reason in _COMPILED:
        if pattern.search(command):
            return reason
    return None
