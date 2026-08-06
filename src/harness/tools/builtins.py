"""Built-in tools. Importing this module registers all tools on the shared
registry instance.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from .. import gitops
from ..workspace import resolve_in_workspace, unified_diff
from .registry import ToolRegistry

_FETCH_TIMEOUT_S = 30
_FETCH_MAX_BYTES = 2_000_000
_FETCH_USER_AGENT = "sun-harness/0.1 (+https://github.com/jetsamsun/sun_agent_harness)"
_FETCH_CACHE_TTL_S = 15 * 60
_FETCH_CACHE_MAX_ENTRIES = 64
# key -> (expires_at, result_without_cached_flag, stored_max_chars)
_FETCH_CACHE: dict[str, tuple[float, dict[str, Any], int]] = {}
_HTML_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template"})
_BLOCK_BREAK_TAGS = frozenset(
    {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}
)

# A single shared registry for the built-in tool set.
registry = ToolRegistry()

# Interactive callbacks injected by the CLI (tests can stub these).
AskFn = Callable[[str], str]
PlanConfirmFn = Callable[[str, list[dict[str, Any]]], tuple[bool, str]]
# (path, diff) -> approved
EditConfirmFn = Callable[[str, str], bool]
_ASK_FN: list[AskFn | None] = [None]
_PLAN_CONFIRM_FN: list[PlanConfirmFn | None] = [None]
_EDIT_CONFIRM_FN: list[EditConfirmFn | None] = [None]
_CONFIRM_EDITS: list[bool] = [False]

# Per-task planning state (reset at the start of each AgentLoop.run).
_TODOS: list[dict[str, Any]] = []
_PLAN: dict[str, Any] | None = None

_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
}


@registry.tool(dangerous=True)
def run_shell(command: str) -> dict:
    """Execute a shell command and return its output.

    Use this to inspect the system, run programs, and accomplish tasks.
    The command runs in a real shell; chain with && or | as needed.

    :param command: The shell command line to execute.
    """
    # Timeout is injected by the executor via functools.partial-style config;
    # here we keep a safe default and let the executor override.
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SHELL_TIMEOUT[0],
        )
        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {_SHELL_TIMEOUT[0]}s",
        }


def _as_int(value: object, default: int) -> int:
    """Coerce tool args that models often send as JSON strings."""
    if value is None or value == "":
        return default
    return int(value)  # type: ignore[arg-type]


@registry.tool()
def read_file(path: str, offset: int = 1, limit: int = 0) -> dict:
    """Read a text file with 1-indexed line numbers.

    Use offset/limit to read a slice of large files (limit=0 means read to end).

    :param path: Path to the file to read.
    :param offset: 1-indexed start line (default 1).
    :param limit: Max number of lines to return; 0 means all remaining lines.
    """
    p = Path(path)
    if not p.exists():
        return {"success": False, "error": f"No such file: {path}"}
    if not p.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}

    try:
        start = max(1, _as_int(offset, 1))
        lim = _as_int(limit, 0)
    except (TypeError, ValueError) as exc:
        return {"success": False, "error": f"offset/limit must be integers: {exc}"}

    total = len(lines)
    if start > total and total > 0:
        return {
            "success": False,
            "error": f"offset {start} past end of file ({total} lines)",
            "total_lines": total,
        }
    begin = start - 1
    end = total if lim <= 0 else min(total, begin + lim)
    chunk = lines[begin:end]
    numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(chunk, start))
    return {
        "success": True,
        "total_lines": total,
        "offset": start,
        "limit": lim,
        "start_line": start if chunk else 0,
        "end_line": start + len(chunk) - 1 if chunk else 0,
        "content": numbered,
    }


def _apply_file_mutation(path: str, new_content: str) -> dict:
    """Shared path sandbox + diff confirm + auto checkpoint + write."""
    p, err = resolve_in_workspace(path)
    if err or p is None:
        return {"success": False, "error": err or "Invalid path"}

    old = ""
    if p.exists():
        if not p.is_file():
            return {"success": False, "error": f"Not a file: {path}"}
        try:
            old = p.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

    if old == new_content:
        return {
            "success": True,
            "path": str(p),
            "bytes_written": len(new_content.encode("utf-8")),
            "noop": True,
            "diff": "(no changes)",
        }

    diff = unified_diff(p, old, new_content)
    if _CONFIRM_EDITS[0]:
        if _EDIT_CONFIRM_FN[0] is None:
            return {
                "success": False,
                "error": "Edit confirmation required but no confirmation channel",
                "path": str(p),
                "diff": diff,
            }
        if not _EDIT_CONFIRM_FN[0](str(p), diff):
            return {
                "success": False,
                "error": "User declined this edit",
                "path": str(p),
                "diff": diff,
            }

    checkpoint = gitops.ensure_auto_checkpoint()

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new_content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}

    out: dict[str, Any] = {
        "success": True,
        "path": str(p),
        "bytes_written": len(new_content.encode("utf-8")),
        "diff": diff,
    }
    if checkpoint is not None:
        out["checkpoint"] = {
            "success": checkpoint.get("success"),
            "sha": checkpoint.get("sha"),
            "skipped": checkpoint.get("skipped"),
            "message": checkpoint.get("message") or checkpoint.get("error"),
        }
    return out


@registry.tool()
def write_file(path: str, content: str) -> dict:
    """Write text to a file, creating parent directories and overwriting any
    existing content. Prefer edit_file for small changes to existing files.

    Paths must stay inside the workspace root. May show a diff for confirmation
    and auto-create a git checkpoint before the first edit of a task.

    :param path: Path to the file to write.
    :param content: The full text content to write.
    """
    return _apply_file_mutation(path, content)


@registry.tool()
def edit_file(path: str, old_text: str, new_text: str) -> dict:
    """Replace exactly one occurrence of old_text with new_text in a file.

    old_text must match uniquely — if zero or multiple matches, the edit fails
    so you can re-read and try a more specific snippet. Prefer this over
    write_file when changing existing code.

    :param path: Path to the file to edit.
    :param old_text: Exact text to find (must appear exactly once).
    :param new_text: Replacement text.
    """
    p, err = resolve_in_workspace(path)
    if err or p is None:
        return {"success": False, "error": err or "Invalid path"}
    if not p.exists():
        return {"success": False, "error": f"No such file: {path}"}
    if not p.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    try:
        original = p.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}

    count = original.count(old_text)
    if count == 0:
        return {
            "success": False,
            "error": "old_text not found; re-read the file and use an exact snippet",
        }
    if count > 1:
        return {
            "success": False,
            "error": f"old_text matched {count} times; provide a more unique snippet",
            "matches": count,
        }
    updated = original.replace(old_text, new_text, 1)
    result = _apply_file_mutation(str(p), updated)
    if result.get("success"):
        result["replacements"] = 1
    return result


@registry.tool()
def search_files(pattern: str, path: str = ".", glob: str = "*") -> dict:
    """Search file contents with a regular expression (like grep).

    Skips common junk dirs (.git, .venv, node_modules, …). Returns matching
    lines with path and 1-indexed line numbers.

    :param pattern: Regular expression to search for.
    :param path: Root directory to search (default current directory).
    :param glob: Filename glob filter, e.g. "*.py" (default all files).
    """
    root = Path(path)
    if not root.exists():
        return {"success": False, "error": f"No such path: {path}"}
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}

    matches: list[dict[str, object]] = []
    files_scanned = 0
    max_matches = 50

    for file_path in _iter_files(root, glob):
        files_scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append({"path": str(file_path), "line": i, "text": line[:300]})
                if len(matches) >= max_matches:
                    return {
                        "success": True,
                        "files_scanned": files_scanned,
                        "match_count": len(matches),
                        "truncated": True,
                        "matches": matches,
                    }

    return {
        "success": True,
        "files_scanned": files_scanned,
        "match_count": len(matches),
        "truncated": False,
        "matches": matches,
    }


@registry.tool()
def find_files(pattern: str, path: str = ".") -> dict:
    """Find files by name glob pattern (e.g. "*.py", "**/test_*.py").

    :param pattern: Glob pattern relative to path.
    :param path: Root directory to search (default current directory).
    """
    root = Path(path)
    if not root.exists():
        return {"success": False, "error": f"No such path: {path}"}

    hits: list[str] = []
    max_hits = 200
    for file_path in root.rglob(pattern):
        if not file_path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in file_path.parts):
            continue
        hits.append(str(file_path))
        if len(hits) >= max_hits:
            return {
                "success": True,
                "count": len(hits),
                "truncated": True,
                "files": hits,
            }
    return {"success": True, "count": len(hits), "truncated": False, "files": hits}


@registry.tool()
def list_dir(path: str = ".") -> dict:
    """List files and subdirectories in a directory (non-recursive).

    :param path: Directory to list (default current directory).
    """
    p = Path(path)
    if not p.exists():
        return {"success": False, "error": f"No such path: {path}"}
    if not p.is_dir():
        return {"success": False, "error": f"Not a directory: {path}"}

    entries: list[dict[str, str]] = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if child.name in _SKIP_DIR_NAMES:
                continue
            kind = "dir" if child.is_dir() else "file"
            entries.append({"name": child.name, "type": kind})
    except OSError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "path": str(p.resolve()), "entries": entries}


class _HtmlTextExtractor(HTMLParser):
    """Minimal HTML → plain text (stdlib only; no JS execution)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._chunks: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in _HTML_SKIP_TAGS:
            self._skip_depth += 1
            return
        if t == "title":
            self._in_title = True
        elif t in _BLOCK_BREAK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in _HTML_SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if t == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        text = data.strip()
        if text:
            self._chunks.append(text + " ")


def _html_to_text(raw: str) -> tuple[str, str]:
    parser = _HtmlTextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    body = re.sub(r"[ \t]+", " ", "".join(parser._chunks))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return title, body


def _decode_http_body(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    if m:
        charset = m.group(1).strip("'\"")
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _normalize_url(url: str) -> str:
    """Canonicalize URL for cache keys (drop fragment; normalize host/path)."""
    p = urlparse(url.strip())
    host = p.netloc.lower()
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = f"?{p.query}" if p.query else ""
    return f"{p.scheme.lower()}://{host}{path}{query}"


def _clamp_max_chars(max_chars: object) -> int:
    try:
        n = int(max_chars)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = 12000
    return max(500, min(n, 100_000))


def clear_fetch_cache() -> None:
    """Clear in-process fetch_url cache (tests / explicit reset)."""
    _FETCH_CACHE.clear()


def _fetch_cache_get(cache_key: str, max_chars: int) -> dict[str, Any] | None:
    entry = _FETCH_CACHE.get(cache_key)
    if entry is None:
        return None
    expires_at, payload, stored_max = entry
    if time.monotonic() >= expires_at:
        _FETCH_CACHE.pop(cache_key, None)
        return None
    # Need a larger window than what we stored and prior response was truncated.
    if max_chars > stored_max and payload.get("truncated"):
        return None
    out = dict(payload)
    text = str(out.get("text", ""))
    if len(text) > max_chars:
        text = text[:max_chars]
        out["text"] = text
        out["truncated"] = True
    out["chars"] = len(text)
    out["cached"] = True
    return out


def _fetch_cache_put(cache_key: str, result: dict[str, Any], max_chars: int) -> None:
    if not result.get("success"):
        return
    # Evict expired / oldest when full.
    now = time.monotonic()
    expired = [k for k, (exp, _, _) in _FETCH_CACHE.items() if now >= exp]
    for k in expired:
        _FETCH_CACHE.pop(k, None)
    while len(_FETCH_CACHE) >= _FETCH_CACHE_MAX_ENTRIES:
        oldest_key = min(_FETCH_CACHE, key=lambda k: _FETCH_CACHE[k][0])
        _FETCH_CACHE.pop(oldest_key, None)
    stored = {k: v for k, v in result.items() if k != "cached"}
    _FETCH_CACHE[cache_key] = (now + _FETCH_CACHE_TTL_S, stored, max_chars)


def _http_get(url: str, *, accept: str) -> tuple[int, str, str, bytes]:
    """GET url; returns (status, final_url, content_type, raw_bytes)."""
    req = Request(
        url,
        headers={
            "User-Agent": _FETCH_USER_AGENT,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        method="GET",
    )
    with urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:  # noqa: S310 — caller checks scheme
        status = getattr(resp, "status", None) or resp.getcode()
        final_url = resp.geturl()
        content_type = resp.headers.get("Content-Type", "") or ""
        raw = resp.read(_FETCH_MAX_BYTES + 1)
    return int(status) if status is not None else 0, final_url, content_type, raw


@registry.tool()
def fetch_url(url: str, max_chars: int = 12000) -> dict:
    """Fetch a web page over HTTP(S) and return readable text for analysis.

    Prefer this over run_shell+curl for reading websites. Does not execute
    JavaScript — SPA / heavily client-rendered pages may return incomplete text.
    Successful responses are reused in-process for ~15 minutes (same URL).

    :param url: http or https URL.
    :param max_chars: Max characters of extracted text to return (default 12000).
    """
    raw_url = url.strip()
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "success": False,
            "error": "Only http/https URLs with a host are allowed",
        }

    max_chars_i = _clamp_max_chars(max_chars)
    cache_key = _normalize_url(raw_url)
    cached = _fetch_cache_get(cache_key, max_chars_i)
    if cached is not None:
        return cached

    try:
        status, final_url, content_type, raw = _http_get(
            raw_url,
            accept=(
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "text/plain,application/json;q=0.8,*/*;q=0.5"
            ),
        )
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read(4000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return {
            "success": False,
            "error": f"HTTP {exc.code}: {exc.reason}",
            "status": exc.code,
            "url": raw_url,
            "body_preview": body[:500],
            "cached": False,
        }
    except URLError as exc:
        return {
            "success": False,
            "error": f"Network error: {exc.reason}",
            "url": raw_url,
            "cached": False,
        }
    except TimeoutError:
        return {
            "success": False,
            "error": f"Timed out after {_FETCH_TIMEOUT_S}s",
            "url": raw_url,
            "cached": False,
        }
    except OSError as exc:
        return {"success": False, "error": str(exc), "url": raw_url, "cached": False}

    truncated_bytes = len(raw) > _FETCH_MAX_BYTES
    if truncated_bytes:
        raw = raw[:_FETCH_MAX_BYTES]

    text = _decode_http_body(raw, content_type)
    ctype_l = content_type.lower()
    title = ""
    if "html" in ctype_l or text.lstrip()[:1] == "<":
        title, text = _html_to_text(text)

    truncated = truncated_bytes or len(text) > max_chars_i
    if len(text) > max_chars_i:
        text = text[:max_chars_i]

    result = {
        "success": True,
        "url": raw_url,
        "final_url": final_url,
        "status": status,
        "content_type": content_type,
        "title": title,
        "text": text,
        "chars": len(text),
        "truncated": truncated,
        "cached": False,
    }
    _fetch_cache_put(cache_key, result, max_chars_i)
    # Also key by final_url after redirects so follow-ups hit cache.
    final_key = _normalize_url(final_url)
    if final_key != cache_key:
        _fetch_cache_put(final_key, result, max_chars_i)
    return result


def _unwrap_ddg_href(href: str) -> str:
    """Expand DuckDuckGo redirect links to the real target URL."""
    h = href.strip()
    if h.startswith("//"):
        h = "https:" + h
    p = urlparse(h)
    if "duckduckgo.com" in p.netloc and (p.path.startswith("/l/") or "/l/?" in h):
        qs = parse_qs(p.query)
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])
    return h


class _DdgResultsParser(HTMLParser):
    """Extract title/url/snippet from DuckDuckGo HTML results page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._in_result_a = False
        self._in_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {k: (v or "") for k, v in attrs}
        classes = set(ad.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._in_result_a = True
            self._title_parts = []
            self._href = ad.get("href", "")
        elif tag in {"a", "td", "div"} and "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_result_a:
            self._in_result_a = False
            url = _unwrap_ddg_href(self._href)
            title = re.sub(r"\s+", " ", "".join(self._title_parts)).strip()
            if url.startswith("http") and title:
                self.results.append({"title": title, "url": url, "snippet": ""})
        elif self._in_snippet and tag in {"a", "td", "div"}:
            self._in_snippet = False
            snippet = re.sub(r"\s+", " ", "".join(self._snippet_parts)).strip()
            if self.results and not self.results[-1].get("snippet"):
                self.results[-1]["snippet"] = snippet[:300]

    def handle_data(self, data: str) -> None:
        if self._in_result_a:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)


@registry.tool()
def search_web(query: str, max_results: int = 5) -> dict:
    """Search the public web for relevant URLs (DuckDuckGo HTML).

    Use when you do not already know the exact documentation URL. Then call
    fetch_url on the single best result — do not guess multiple path variants.

    :param query: Search query (include product/site keywords when useful).
    :param max_results: How many results to return (1–10, default 5).
    """
    q = (query or "").strip()
    if not q:
        return {"success": False, "error": "query must be non-empty"}

    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(n, 10))

    search_url = "https://html.duckduckgo.com/html/?" + urlencode({"q": q})
    try:
        status, final_url, content_type, raw = _http_get(
            search_url,
            accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        )
    except HTTPError as exc:
        return {"success": False, "error": f"HTTP {exc.code}: {exc.reason}", "query": q}
    except URLError as exc:
        return {"success": False, "error": f"Network error: {exc.reason}", "query": q}
    except TimeoutError:
        return {"success": False, "error": f"Timed out after {_FETCH_TIMEOUT_S}s", "query": q}
    except OSError as exc:
        return {"success": False, "error": str(exc), "query": q}

    if status >= 400:
        return {"success": False, "error": f"HTTP {status}", "query": q, "status": status}

    html = _decode_http_body(raw[:_FETCH_MAX_BYTES], content_type)
    parser = _DdgResultsParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass

    # Deduplicate by normalized URL while preserving order.
    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for item in parser.results:
        key = _normalize_url(item["url"])
        if key in seen:
            continue
        seen.add(key)
        results.append(item)
        if len(results) >= n:
            break

    return {
        "success": True,
        "query": q,
        "count": len(results),
        "results": results,
        "search_url": final_url,
        "note": "Pick one best URL and fetch_url it; avoid guessing multiple paths.",
    }


@registry.tool()
def check_syntax(path: str) -> dict:
    """Check that a source file parses without running it.

    Supports .py (python -m py_compile). Use after write_file/edit_file.

    :param path: Path to the source file.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {"success": False, "passed": False, "error": f"No such file: {path}"}

    suffix = p.suffix.lower()
    if suffix == ".py":
        cmd = f'python -m py_compile "{p}"'
    elif suffix in {".js", ".mjs", ".cjs"}:
        cmd = f'node --check "{p}"'
    else:
        return {
            "success": False,
            "passed": False,
            "error": f"No syntax checker wired for {suffix or '(no extension)'}",
        }

    proc = _run_capture(cmd, timeout=60)
    passed = proc["exit_code"] == 0
    return {
        "success": passed,
        "passed": passed,
        "path": str(p),
        "command": cmd,
        "stdout": proc["stdout"],
        "stderr": proc["stderr"],
        "exit_code": proc["exit_code"],
    }


@registry.tool()
def run_tests(command: str = "") -> dict:
    """Run the project's test suite and return a structured pass/fail summary.

    If command is empty, auto-detect (pytest / npm test / go test). On failure,
    fix the code and call run_tests again — do not finish while tests are red.

    :param command: Explicit test command; leave empty to auto-detect.
    """
    cwd = Path.cwd()
    cmd = command.strip() or _detect_test_command(cwd)
    if not cmd:
        return {
            "success": False,
            "passed": False,
            "error": "Could not auto-detect tests; pass an explicit command",
        }

    proc = _run_capture(cmd, timeout=max(_SHELL_TIMEOUT[0], 120))
    passed = proc["exit_code"] == 0
    output = _join_output(proc["stdout"], proc["stderr"])
    return {
        "success": passed,
        "passed": passed,
        "command": cmd,
        "exit_code": proc["exit_code"],
        "summary": _test_summary(output, passed),
        "output": output[:6000],
    }


@registry.tool()
def run_lint(command: str = "") -> dict:
    """Run a linter / static checker (ruff, eslint, …).

    If command is empty, auto-detect a sensible default for the repo.

    :param command: Explicit lint command; leave empty to auto-detect.
    """
    cwd = Path.cwd()
    cmd = command.strip() or _detect_lint_command(cwd)
    if not cmd:
        return {
            "success": False,
            "passed": False,
            "error": "Could not auto-detect a linter; pass an explicit command",
        }

    proc = _run_capture(cmd, timeout=max(_SHELL_TIMEOUT[0], 120))
    passed = proc["exit_code"] == 0
    output = _join_output(proc["stdout"], proc["stderr"])
    return {
        "success": passed,
        "passed": passed,
        "command": cmd,
        "exit_code": proc["exit_code"],
        "output": output[:6000],
    }


@registry.tool()
def ask_user(question: str) -> dict:
    """Ask the human a clarifying question and wait for their reply.

    Use when the task is ambiguous (scope, acceptance criteria, stack, paths).
    Prefer one focused question at a time.

    :param question: The question to show the user.
    """
    if not sys.stdin.isatty() or _ASK_FN[0] is None:
        return {
            "success": False,
            "error": "No interactive TTY for ask_user; cannot clarify",
        }
    answer = _ASK_FN[0](question)
    return {"success": True, "question": question, "answer": answer}


@registry.tool()
def propose_plan(title: str, steps_json: str) -> dict:
    """Propose a multi-step plan with per-step acceptance criteria for approval.

    steps_json must be a JSON array of objects:
    [{"id":"1","title":"...","acceptance":"..."}, ...]
    Do not start coding edits until approved=true. If rejected, revise and call
    propose_plan again (or ask_user for more constraints).

    :param title: Short plan title.
    :param steps_json: JSON array of steps with id/title/acceptance.
    """
    global _PLAN
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        return {"success": False, "approved": False, "error": f"Invalid steps_json: {exc}"}
    if not isinstance(steps, list) or not steps:
        return {
            "success": False,
            "approved": False,
            "error": "steps_json must be a non-empty array",
        }

    cleaned: list[dict[str, Any]] = []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            return {"success": False, "approved": False, "error": f"step {i} must be an object"}
        cleaned.append(
            {
                "id": str(step.get("id") or i),
                "title": str(step.get("title") or "").strip() or f"Step {i}",
                "acceptance": str(step.get("acceptance") or "").strip() or "(unspecified)",
            }
        )

    if not sys.stdin.isatty() or _PLAN_CONFIRM_FN[0] is None:
        return {
            "success": False,
            "approved": False,
            "error": "No interactive TTY for propose_plan; cannot get approval",
            "title": title,
            "steps": cleaned,
        }

    approved, note = _PLAN_CONFIRM_FN[0](title, cleaned)
    _PLAN = {
        "title": title,
        "steps": cleaned,
        "approved": approved,
        "note": note,
    }
    return {
        "success": True,
        "approved": approved,
        "note": note,
        "title": title,
        "steps": cleaned,
        "hint": (
            "Plan approved — mirror steps into todo_write and execute."
            if approved
            else "Plan rejected — revise via ask_user/propose_plan before editing."
        ),
    }


@registry.tool()
def todo_write(items_json: str) -> dict:
    """Replace the in-memory todo list for this task (progress checklist).

    items_json: JSON array of
    {"id":"1","content":"...","status":"pending|in_progress|done|cancelled"}
    Keep exactly one item in_progress. Call after plan approval and as you go.

    :param items_json: JSON array of todo items.
    """
    global _TODOS
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"Invalid items_json: {exc}"}
    if not isinstance(items, list):
        return {"success": False, "error": "items_json must be a JSON array"}

    allowed = {"pending", "in_progress", "done", "cancelled"}
    cleaned: list[dict[str, Any]] = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return {"success": False, "error": f"item {i} must be an object"}
        status = str(item.get("status") or "pending").lower()
        if status not in allowed:
            return {"success": False, "error": f"item {i}: invalid status {status}"}
        cleaned.append(
            {
                "id": str(item.get("id") or i),
                "content": str(item.get("content") or "").strip() or f"Item {i}",
                "status": status,
            }
        )

    in_progress = sum(1 for t in cleaned if t["status"] == "in_progress")
    if in_progress > 1:
        return {"success": False, "error": "At most one todo may be in_progress"}

    _TODOS = cleaned
    done = sum(1 for t in cleaned if t["status"] == "done")
    return {
        "success": True,
        "count": len(cleaned),
        "done": done,
        "todos": cleaned,
    }


@registry.tool()
def git_checkpoint(message: str = "") -> dict:
    """Create a git checkpoint (commit dirty changes, or record clean HEAD).

    Call before risky edits if you want an explicit restore point. The harness
    also auto-checkpoints once before the first write/edit of a task when enabled.

    :param message: Optional commit message for a dirty-tree checkpoint.
    """
    return gitops.create_checkpoint(message)


@registry.tool(dangerous=True)
def git_rollback(sha: str = "") -> dict:
    """Hard-reset the repo to a checkpoint SHA (default: last Sun checkpoint).

    Destructive — reset --hard + clean -fd: discards later commits, dirty and
    untracked files. Prefer after a bad edit series. May require confirmation.

    :param sha: Checkpoint SHA; empty uses the last auto/manual Sun checkpoint.
    """
    return gitops.rollback_to_checkpoint(sha or None)


@registry.tool()
def git_commit(message: str) -> dict:
    """Stage all changes and create a git commit with the given message.

    Use after verification is green when the user asked to save/commit work.

    :param message: Commit message (required, non-empty).
    """
    return gitops.commit_all(message)


@registry.tool()
def finish(summary: str) -> dict:
    """Declare the task complete. Call this ONLY when the user's task is fully
    accomplished and verification is green (tests/syntax as applicable).

    finish ends the current task turn and returns to the sun> prompt.
    To leave the interactive REPL entirely (用户说退出/再见/quit), call
    exit_repl instead — do not only say goodbye in text.

    :param summary: A short summary of the outcome for the user.
    """
    return {"success": True, "finished": True, "summary": summary}


@registry.tool()
def exit_repl(farewell: str = "") -> dict:
    """Exit the interactive Sun REPL process. Use when the user asks to leave,
    quit, 退出, 再见, bye, etc.

    Do NOT use finish for that — finish only ends the current task and shows
    sun> again. Prefer calling this tool over answering with text alone.

    :param farewell: Optional short goodbye shown before the process exits.
    """
    text = (farewell or "").strip() or "已退出。"
    return {
        "success": True,
        "quit_repl": True,
        "finished": True,
        "summary": text,
    }


@registry.tool()
def session_search(
    query: str = "",
    session_id: str = "",
    limit: int = 15,
) -> dict:
    """Search past Redis chat transcripts for this working directory.

    Use when the user asks about prior conversations — e.g.「之前说过」「你还记得」
    「什么秘密」「上回」「昨天聊了什么」— or anything that may live in chat
    history but NOT in SQLite long memory. Do NOT rely on memory_list alone for
    that; call this first (or as well).

    Empty query returns recent user messages from recent sessions.
    Requires SUN_REDIS_URL; otherwise reports unavailable.

    :param query: Substring keywords (case-insensitive). Empty = recent user lines.
    :param session_id: Optional session id to search only that transcript.
    :param limit: Max matching snippets (1–40).
    """
    return _session_search_impl(query=query, session_id=session_id, limit=limit)


@registry.tool()
def list_models() -> dict:
    """Report the currently configured Sun model and list models available on
    the configured OpenAI-compatible endpoint (GET /models).

    Use this immediately when the user asks which model is in use, what models
    are available, or how to switch models. Do NOT probe env vars or .env via
    run_shell / read_file for that question.

    No parameters.
    """
    return fetch_model_status()


@registry.tool()
def memory_list(kind: str = "") -> dict:
    """List durable memory and return a ready-to-show dump.

    Trigger on natural language such as「看记忆库」「列出记忆」「记忆里有什么」.
    Present the `formatted` field to the user AS-IS (never rewrite as a table).

    Format of `formatted`:
      【系统提示词】… / [开发环境]… / 【铁律】… / 【人格】… / 【项目背景】… / 【其他】…

    Kinds: system, iron, dev_env, persona, project, other (Chinese labels ok).
    Omit kind to dump all sections.

    :param kind: Optional kind filter (English key or Chinese label).
    """
    from ..long_memory import LongMemoryError, open_long_memory

    mem = open_long_memory(_SQLITE_PATH[0])
    try:
        try:
            k = kind.strip() or None
            entries = mem.list(kind=k)
            formatted = mem.format_dump(kind=k)
        except LongMemoryError as exc:
            return {"success": False, "error": str(exc), "kinds": list(_memory_kinds())}
        return {
            "success": True,
            "db": str(mem.path),
            "kinds": _memory_kinds(),
            "count": len(entries),
            "formatted": formatted,
            "entries": [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "kind_label": e.as_dict()["kind_label"],
                    "key": e.key,
                    "title": e.title,
                    "updated_at": e.updated_at,
                    "content": e.content,
                }
                for e in entries
            ],
        }
    finally:
        mem.close()


@registry.tool()
def memory_get(entry_id: int = 0, kind: str = "", key: str = "") -> dict:
    """Get one durable memory entry by id, or by kind+key.

    Use for natural language like「我的人格写了什么」「看一下铁律」.

    :param entry_id: Numeric id from memory_list (preferred).
    :param kind: Kind when looking up by key (system/iron/dev_env/persona/project/other).
    :param key: Key within kind (default is often \"default\").
    """
    from ..long_memory import LongMemoryError, open_long_memory

    mem = open_long_memory(_SQLITE_PATH[0])
    try:
        try:
            if entry_id and int(entry_id) > 0:
                entry = mem.get(int(entry_id))
            elif kind.strip() and key.strip():
                entry = mem.get_by_key(kind, key)
            elif kind.strip():
                entry = mem.get_by_key(kind, "default")
            else:
                return {
                    "success": False,
                    "error": "Provide entry_id, or kind (+ optional key)",
                }
        except LongMemoryError as exc:
            return {"success": False, "error": str(exc)}
        if entry is None:
            return {"success": False, "error": "not found"}
        return {"success": True, "db": str(mem.path), "entry": entry.as_dict()}
    finally:
        mem.close()


@registry.tool()
def memory_upsert(
    kind: str,
    content: str,
    key: str = "default",
    title: str = "",
) -> dict:
    """Create or update a durable memory entry (SQLite).

    Use for natural language like「记住…」「加一条铁律…」「更新人格为…」.
    Kinds: system / iron / dev_env / persona / project / other (Chinese labels also ok).
    Changes apply to the next task's system prompt (current turn already loaded).

    :param kind: Memory category.
    :param content: Full text to store (non-empty).
    :param key: Slug within kind; unique per kind. Default \"default\".
    :param title: Optional short title.
    """
    from ..long_memory import LongMemoryError, open_long_memory

    mem = open_long_memory(_SQLITE_PATH[0])
    try:
        try:
            entry = mem.upsert(kind=kind, key=key, content=content, title=title)
        except LongMemoryError as exc:
            return {"success": False, "error": str(exc), "kinds": list(_memory_kinds())}
        return {
            "success": True,
            "db": str(mem.path),
            "entry": entry.as_dict(),
            "note": "已保存；下一轮任务组装 system prompt 时生效。",
        }
    finally:
        mem.close()


@registry.tool(dangerous=True)
def memory_delete(entry_id: int) -> dict:
    """Delete one durable memory entry by id. Requires user confirmation.

    Use for natural language like「删掉某条记忆」; list/get first if id unknown.
    Does not affect Redis chat sessions.

    :param entry_id: Entry id to delete.
    """
    from ..long_memory import open_long_memory

    eid = int(entry_id)
    mem = open_long_memory(_SQLITE_PATH[0])
    try:
        entry = mem.get(eid)
        if entry is None:
            return {"success": False, "error": f"not found id={eid}"}
        ok = mem.delete(eid)
        return {
            "success": ok,
            "deleted_id": eid,
            "deleted": entry.as_dict() if ok else None,
            "db": str(mem.path),
        }
    finally:
        mem.close()


def _memory_kinds() -> list[dict[str, str]]:
    from ..long_memory import KINDS, _KIND_LABELS

    return [{"kind": k, "label": _KIND_LABELS[k]} for k in KINDS]


# LLM connection snapshot for tools that need base_url / api_key / model.
_LLM_CONFIG: dict[str, str] = {"api_key": "", "base_url": "", "model": ""}
# SQLite path for memory_* tools (empty = project ./long_memory.db).
_SQLITE_PATH = [""]
# Redis SessionStore for session_search (None = Redis not configured).
_SESSION_STORE: list[Any] = [None]

_SESSION_SEARCH_EXCERPT = 420
_SESSION_SEARCH_MAX_SESSIONS = 40


def set_llm_config(*, api_key: str, base_url: str, model: str) -> None:
    """Inject current Settings into tools (called from CLI bootstrap)."""
    _LLM_CONFIG["api_key"] = api_key or ""
    _LLM_CONFIG["base_url"] = (base_url or "").rstrip("/")
    _LLM_CONFIG["model"] = model or ""


def set_sqlite_path(path: str) -> None:
    """Inject SUN_SQLITE_PATH (or empty for default) into memory_* tools."""
    _SQLITE_PATH[0] = (path or "").strip()


def set_session_store(store: Any | None) -> None:
    """Inject Redis SessionStore (or None) for session_search."""
    _SESSION_STORE[0] = store


def _transcript_line_text(msg: dict[str, Any]) -> str:
    """Plain text from a transcript message; skip tool/system noise."""
    role = str(msg.get("role") or "")
    if role in {"system", "tool"}:
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        text = content.strip()
        if text:
            return text
    return ""


def _session_search_impl(
    *,
    query: str = "",
    session_id: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    store = _SESSION_STORE[0]
    if store is None:
        return {
            "success": False,
            "error": "Redis session store unavailable (set SUN_REDIS_URL to search chat history).",
            "matches": [],
            "count": 0,
        }
    q = (query or "").strip()
    sid_filter = (session_id or "").strip()
    try:
        lim = max(1, min(int(limit or 15), 40))
    except (TypeError, ValueError):
        lim = 15

    cwd = str(Path.cwd().resolve())
    try:
        if sid_filter:
            meta = store.get_meta(sid_filter)
            if meta is None:
                return {
                    "success": False,
                    "error": f"session not found: {sid_filter}",
                    "matches": [],
                    "count": 0,
                }
            if meta.cwd != cwd:
                return {
                    "success": False,
                    "error": (
                        f"session {sid_filter} belongs to another cwd "
                        f"({meta.cwd}); cd there or omit session_id."
                    ),
                    "matches": [],
                    "count": 0,
                }
            sessions = [meta]
        else:
            sessions = store.list_sessions(cwd=cwd, limit=_SESSION_SEARCH_MAX_SESSIONS)
    except Exception as exc:  # noqa: BLE001 — surface Redis errors to model
        return {
            "success": False,
            "error": f"session search failed: {exc}",
            "matches": [],
            "count": 0,
        }

    q_low = q.lower()
    matches: list[dict[str, Any]] = []
    sessions_scanned = 0

    for meta in sessions:
        sessions_scanned += 1
        try:
            transcript = store.get_transcript(meta.id)
        except Exception:  # noqa: BLE001
            continue
        for idx, msg in enumerate(transcript):
            role = str(msg.get("role") or "")
            text = _transcript_line_text(msg if isinstance(msg, dict) else {})
            if not text:
                continue
            if q:
                if q_low not in text.lower() and q not in text:
                    continue
            elif role != "user":
                # Empty query: recent user lines only.
                continue
            excerpt = text if len(text) <= _SESSION_SEARCH_EXCERPT else (
                text[: _SESSION_SEARCH_EXCERPT - 1] + "…"
            )
            matches.append(
                {
                    "session_id": meta.id,
                    "title": meta.title,
                    "updated_at": meta.updated_at,
                    "status": meta.status,
                    "role": role,
                    "index": idx,
                    "excerpt": excerpt,
                }
            )
            if len(matches) >= lim:
                break
        if len(matches) >= lim:
            break

    # Empty query: prefer newest first (list_sessions already newest-first;
    # within a session we walked top-to-bottom — reverse for "recent").
    if not q and matches:
        matches = list(reversed(matches))[:lim]

    hint = (
        "Matches are from Redis chat transcripts for this cwd. "
        "Long-term SQLite memory is separate (memory_list)."
    )
    if not matches and q:
        hint += " No keyword hits; try broader query or empty query for recent user lines."

    return {
        "success": True,
        "query": q,
        "cwd": cwd,
        "sessions_scanned": sessions_scanned,
        "count": len(matches),
        "matches": matches,
        "hint": hint,
    }


def fetch_model_status(*, timeout_s: float = 15.0) -> dict[str, Any]:
    """Return current model + /v1/models listing (shared by tool and /models)."""
    api_key = _LLM_CONFIG.get("api_key", "")
    base_url = _LLM_CONFIG.get("base_url", "").rstrip("/")
    current = _LLM_CONFIG.get("model", "")
    out: dict[str, Any] = {
        "success": True,
        "current_model": current,
        "base_url": base_url,
        "available_models": [],
        "count": 0,
        "switch_hint": "修改 .env 或 ~/.config/sun/config.toml 的 SUN_MODEL（或 sun model），下一轮生效。",
    }
    if not base_url:
        out["success"] = False
        out["error"] = "base_url not configured"
        return out
    if not api_key:
        out["success"] = False
        out["error"] = "api_key not configured"
        return out

    url = f"{base_url}/models"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _FETCH_USER_AGENT,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read(512_000).decode("utf-8", errors="replace")
        payload = json.loads(raw)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:  # noqa: BLE001
            detail = str(exc)
        out["success"] = False
        out["error"] = f"HTTP {exc.code}: {detail or exc.reason}"
        return out
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        out["success"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        out["success"] = False
        out["error"] = "unexpected /models response shape"
        out["raw_keys"] = list(payload.keys()) if isinstance(payload, dict) else []
        return out

    models: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        entry: dict[str, Any] = {
            "id": mid,
            "owned_by": item.get("owned_by") or "",
            "current": mid == current,
        }
        endpoints = item.get("supported_endpoint_types")
        if endpoints:
            entry["supported_endpoint_types"] = endpoints
        models.append(entry)
    models.sort(key=lambda m: (not m["current"], m["id"]))
    out["available_models"] = models
    out["count"] = len(models)
    return out


def set_ask_fn(fn: AskFn | None) -> None:
    _ASK_FN[0] = fn


def set_plan_confirm_fn(fn: PlanConfirmFn | None) -> None:
    _PLAN_CONFIRM_FN[0] = fn


def set_edit_confirm_fn(fn: EditConfirmFn | None) -> None:
    _EDIT_CONFIRM_FN[0] = fn


def set_confirm_edits(enabled: bool) -> None:
    _CONFIRM_EDITS[0] = enabled


def reset_planning_state() -> None:
    """Clear todos/plan/git bookkeeping between tasks (called by AgentLoop.run)."""
    global _TODOS, _PLAN
    _TODOS = []
    _PLAN = None
    gitops.reset_git_state()


def export_planning_state() -> dict[str, Any]:
    """Snapshot todos/plan for session persistence."""
    return {"todos": list(_TODOS), "plan": _PLAN}


def import_planning_state(data: dict[str, Any] | None) -> None:
    """Restore todos/plan from a persisted session (does not touch git state)."""
    global _TODOS, _PLAN
    if not data:
        _TODOS = []
        _PLAN = None
        return
    todos = data.get("todos")
    _TODOS = list(todos) if isinstance(todos, list) else []
    plan = data.get("plan")
    _PLAN = plan if isinstance(plan, dict) else None


def _run_capture(command: str, timeout: int) -> dict:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
        }


def _join_output(stdout: str, stderr: str) -> str:
    parts = [p for p in (stdout.strip(), stderr.strip()) if p]
    return "\n".join(parts)


def _detect_test_command(cwd: Path) -> str | None:
    if (cwd / "pytest.ini").exists() or (cwd / "conftest.py").exists():
        return "python -m pytest -q"
    if (cwd / "pyproject.toml").exists():
        text = (cwd / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        if "pytest" in text or (cwd / "tests").is_dir():
            return "python -m pytest -q"
    if (cwd / "package.json").exists():
        text = (cwd / "package.json").read_text(encoding="utf-8", errors="replace")
        if '"test"' in text:
            return "npm test --silent"
    if (cwd / "go.mod").exists():
        return "go test ./..."
    if any(cwd.glob("test_*.py")) or any(cwd.glob("*_test.py")):
        return "python -m pytest -q"
    return None


def _detect_lint_command(cwd: Path) -> str | None:
    if (cwd / "pyproject.toml").exists() or (cwd / "ruff.toml").exists():
        text = ""
        cfg = cwd / "pyproject.toml"
        if cfg.exists():
            text = cfg.read_text(encoding="utf-8", errors="replace")
        if "ruff" in text or (cwd / "ruff.toml").exists():
            return "python -m ruff check ."
    if (cwd / "package.json").exists():
        text = (cwd / "package.json").read_text(encoding="utf-8", errors="replace")
        if '"lint"' in text:
            return "npm run lint --silent"
    return None


def _test_summary(output: str, passed: bool) -> str:
    if passed:
        return "PASS"
    lines = [ln for ln in output.splitlines() if ln.strip()]
    # Prefer pytest failure headers / assertion lines.
    interesting = [
        ln
        for ln in lines
        if ln.startswith("FAILED")
        or ln.startswith("ERROR")
        or "AssertionError" in ln
        or ln.strip().startswith("E ")
    ]
    if interesting:
        return "FAIL: " + " | ".join(interesting[:8])
    tail = lines[-12:] if lines else ["(no output)"]
    return "FAIL: " + " | ".join(tail)


def _iter_files(root: Path, glob_pat: str) -> Iterator[Path]:
    if root.is_file():
        if fnmatch.fnmatch(root.name, glob_pat):
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for name in filenames:
            if not fnmatch.fnmatch(name, glob_pat):
                continue
            yield Path(dirpath) / name


# Mutable holder so the executor can set the configured timeout without
# threading it through every call signature.
_SHELL_TIMEOUT = [60]


def set_shell_timeout(seconds: int) -> None:
    _SHELL_TIMEOUT[0] = seconds
