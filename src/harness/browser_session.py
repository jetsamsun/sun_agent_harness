"""Playwright browser session for Sun (headed by default).

Optional dependency: `pip/uv install playwright` then `playwright install chromium`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CFG: dict[str, Any] = {
    "enabled": True,
    "headless": False,
    "timeout_ms": 30_000,
    "screenshot_dir": "",
}


@dataclass
class PageInfo:
    url: str
    title: str


class BrowserSessionError(RuntimeError):
    pass


class BrowserSession:
    """Process-wide single browser/page (REPL-friendly)."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    @property
    def open(self) -> bool:
        return self._page is not None

    def ensure(self) -> Any:
        if not _CFG.get("enabled", True):
            raise BrowserSessionError(
                "Browser disabled (SUN_BROWSER_ENABLED=false)."
            )
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserSessionError(
                "Playwright not installed. Run: "
                "uv sync --extra browser && uv run playwright install chromium"
            ) from exc
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=bool(_CFG.get("headless", False))
            )
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            self._context.set_default_timeout(int(_CFG.get("timeout_ms") or 30_000))
            self._page = self._context.new_page()
        except Exception as exc:
            self.close()
            msg = str(exc)
            if "Executable doesn't exist" in msg or "browserType.launch" in msg:
                raise BrowserSessionError(
                    "Chromium not installed for Playwright. Run: "
                    "uv run playwright install chromium"
                ) from exc
            raise BrowserSessionError(f"Failed to start browser: {exc}") from exc
        return self._page

    def goto(self, url: str) -> PageInfo:
        page = self.ensure()
        page.goto(url, wait_until="domcontentloaded")
        return PageInfo(url=page.url, title=page.title())

    def info(self) -> PageInfo | None:
        if self._page is None:
            return None
        return PageInfo(url=self._page.url, title=self._page.title())

    def snapshot(self, *, max_chars: int = 12_000) -> str:
        page = self.ensure()
        text = ""
        try:
            text = page.locator("body").aria_snapshot()
        except Exception:
            text = ""
        if not text.strip():
            text = self._fallback_snapshot(page)
        text = text.strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text

    def _fallback_snapshot(self, page: Any) -> str:
        bits = page.evaluate(
            """() => {
              const out = [];
              out.push('url: ' + location.href);
              out.push('title: ' + document.title);
              const nodes = document.querySelectorAll(
                'a[href], button, input, textarea, select, [role="button"]'
              );
              let i = 0;
              for (const el of nodes) {
                if (i++ > 80) { out.push('…'); break; }
                const tag = el.tagName.toLowerCase();
                const type = el.getAttribute('type') || '';
                const name = el.getAttribute('name') || '';
                const id = el.id || '';
                const role = el.getAttribute('role') || '';
                const placeholder = el.getAttribute('placeholder') || '';
                const text = (el.innerText || el.value || '').trim().slice(0, 60);
                const href = el.getAttribute('href') || '';
                out.push(
                  `- ${tag}` +
                  (type ? ` type=${type}` : '') +
                  (name ? ` name=${name}` : '') +
                  (id ? ` id=${id}` : '') +
                  (role ? ` role=${role}` : '') +
                  (placeholder ? ` placeholder=${placeholder}` : '') +
                  (href ? ` href=${href}` : '') +
                  (text ? ` text=${JSON.stringify(text)}` : '')
                );
              }
              return out.join('\\n');
            }"""
        )
        return str(bits or "")

    def click(self, selector: str) -> PageInfo:
        page = self.ensure()
        page.locator(selector).first.click()
        return PageInfo(url=page.url, title=page.title())

    def fill(self, selector: str, text: str) -> PageInfo:
        page = self.ensure()
        loc = page.locator(selector).first
        loc.fill(text)
        return PageInfo(url=page.url, title=page.title())

    def press(self, key: str, selector: str = "") -> PageInfo:
        page = self.ensure()
        if selector.strip():
            page.locator(selector).first.press(key)
        else:
            page.keyboard.press(key)
        return PageInfo(url=page.url, title=page.title())

    def wait(
        self,
        *,
        selector: str = "",
        url_contains: str = "",
        timeout_ms: int | None = None,
    ) -> PageInfo:
        page = self.ensure()
        t = int(timeout_ms if timeout_ms is not None else _CFG.get("timeout_ms") or 30_000)
        if selector.strip():
            page.locator(selector).first.wait_for(state="visible", timeout=t)
        if url_contains.strip():
            page.wait_for_url(
                re.compile(".*" + re.escape(url_contains) + ".*"),
                timeout=t,
            )
        if not selector.strip() and not url_contains.strip():
            page.wait_for_timeout(min(t, 2000))
        return PageInfo(url=page.url, title=page.title())

    def screenshot(self, path: str | Path | None = None) -> Path:
        page = self.ensure()
        if path:
            out = Path(path)
        else:
            base = _CFG.get("screenshot_dir") or ""
            root = Path(base) if base else Path.cwd() / ".sun" / "screenshots"
            root.mkdir(parents=True, exist_ok=True)
            out = root / f"shot_{int(time.time())}.png"
        out = out.expanduser()
        if not out.is_absolute():
            out = (Path.cwd() / out).resolve()
        else:
            out = out.resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page=False)
        return out

    def close(self) -> None:
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None


_SESSION = BrowserSession()


def get_browser_session() -> BrowserSession:
    return _SESSION


def set_browser_config(
    *,
    enabled: bool = True,
    headless: bool = False,
    timeout_ms: int = 30_000,
    screenshot_dir: str = "",
) -> None:
    _CFG["enabled"] = bool(enabled)
    _CFG["headless"] = bool(headless)
    _CFG["timeout_ms"] = max(1000, int(timeout_ms or 30_000))
    _CFG["screenshot_dir"] = (screenshot_dir or "").strip()


def close_browser() -> None:
    _SESSION.close()
