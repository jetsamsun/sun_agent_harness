"""Safety layer: detect dangerous shell commands and gate them behind
an interactive confirmation.

Stage 1 uses a simple regex blocklist. It is intentionally conservative —
false positives (asking for confirmation unnecessarily) are acceptable;
false negatives (running something destructive silently) are not.
"""

from __future__ import annotations

import re

# Patterns that should trigger a confirmation prompt before running.
# Any file/directory delete path must be listed here — silent deletes are not OK.
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # --- deletes (always confirm) ---
    (r"\brm\b", "delete files (rm)"),
    (r"\bunlink\b", "delete file (unlink)"),
    (r"\brmdir\b", "delete directory (rmdir)"),
    (r"(?i)\bdel\b", "delete files (del)"),
    (r"(?i)\berase\b", "delete files (erase)"),
    (r"(?i)\brd\b", "delete directory (rd)"),
    (r"(?i)\bRemove-Item\b", "delete files (Remove-Item)"),
    (r"\bgit\s+rm\b", "delete files (git rm)"),
    (r"\bos\.(remove|unlink)\b", "delete file (os.remove/unlink)"),
    (r"\bshutil\.rmtree\b", "delete tree (shutil.rmtree)"),
    (r"\.unlink\s*\(", "delete file (Path.unlink)"),
    (r"\bPath\s*\([^)]*\)\s*\.unlink\b", "delete file (Path.unlink)"),
    # --- other destructive ops ---
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

# Narrow allowlist: agent-created helpers like `_tmp_mall_shop_links.php`.
_TMP_HELPER = re.compile(r"(?:[\\/]|^|['\"\s])_tmp_[A-Za-z0-9_.-]+\b", re.I)
_TMP_DELETE_VERB = re.compile(
    r"(?i)\b(?:del|rm|Remove-Item|os\.(?:remove|unlink))\b|\.unlink\s*\("
)
# Reject recursive / tree deletes even when the path contains `_tmp_*`.
_TMP_DELETE_BROAD = re.compile(
    r"(?i)(?:/\s*s\b|-Recurse\b|--recursive\b|\brm\s+-[a-zA-Z]*r)"
)


def _is_safe_tmp_helper_delete(command: str) -> bool:
    """Allow single-file cleanup of `_tmp_*` helpers without confirmation."""
    if not _TMP_HELPER.search(command):
        return False
    if not _TMP_DELETE_VERB.search(command):
        return False
    if _TMP_DELETE_BROAD.search(command):
        return False
    return True


def assess_command(command: str) -> str | None:
    """Return a human-readable reason if the command looks dangerous, else None."""
    if _is_safe_tmp_helper_delete(command):
        return None
    for pattern, reason in _COMPILED:
        if pattern.search(command):
            return reason
    return None
