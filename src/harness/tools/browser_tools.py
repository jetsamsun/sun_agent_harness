"""Browser automation tools (Playwright). Registers on the shared builtins registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..browser_session import (
    BrowserSessionError,
    close_browser,
    get_browser_session,
)
from ..workspace import resolve_in_workspace
from .builtins import registry


def _page_dict(info: Any) -> dict[str, str]:
    return {"url": info.url, "title": info.title}


def _err(exc: Exception) -> dict[str, Any]:
    return {"success": False, "error": str(exc)}


@registry.tool()
def browser_open(url: str) -> dict:
    """Open a URL in the Sun-managed browser (headed Chromium by default).

    Use after secret_vault_get when logging into a site. Keeps one browser
    session for follow-up click/fill/screenshot tools.

    :param url: Full http(s) URL to open.
    """
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return {"success": False, "error": "url must start with http:// or https://"}
    try:
        info = get_browser_session().goto(u)
    except BrowserSessionError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return {"success": True, **_page_dict(info)}


@registry.tool()
def browser_snapshot() -> dict:
    """Return an accessibility / interactive-element snapshot of the current page.

    Call before click/fill to choose selectors. Prefer role/name/CSS from this
    snapshot over guessing.
    """
    try:
        session = get_browser_session()
        if not session.open:
            return {"success": False, "error": "No browser page open; call browser_open first"}
        text = session.snapshot()
        info = session.info()
    except BrowserSessionError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    out: dict[str, Any] = {"success": True, "snapshot": text}
    if info:
        out.update(_page_dict(info))
    return out


@registry.tool()
def browser_click(selector: str) -> dict:
    """Click the first element matching a Playwright selector.

    :param selector: CSS / text / role selector, e.g. \"button:has-text('登录')\"
      or \"#login\" or \"input[name='username']\".
    """
    sel = (selector or "").strip()
    if not sel:
        return {"success": False, "error": "selector must be non-empty"}
    try:
        info = get_browser_session().click(sel)
    except BrowserSessionError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return {"success": True, "clicked": sel, **_page_dict(info)}


@registry.tool()
def browser_fill(selector: str, text: str, secret: bool = False) -> dict:
    """Fill an input/textarea. Set secret=true for passwords (value not echoed).

    Never print passwords in your final reply to the user.

    :param selector: Playwright selector for the field.
    :param text: Value to type.
    :param secret: If true, tool result omits the filled value.
    """
    sel = (selector or "").strip()
    if not sel:
        return {"success": False, "error": "selector must be non-empty"}
    try:
        info = get_browser_session().fill(sel, text if text is not None else "")
    except BrowserSessionError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    out: dict[str, Any] = {
        "success": True,
        "filled": sel,
        "secret": bool(secret),
        **_page_dict(info),
    }
    if not secret:
        out["value_len"] = len(text or "")
    else:
        out["value"] = "[redacted]"
    return out


@registry.tool()
def browser_press(key: str, selector: str = "") -> dict:
    """Press a keyboard key (e.g. Enter, Tab). Optional selector focuses first.

    :param key: Playwright key name, e.g. \"Enter\".
    :param selector: Optional element to focus before pressing.
    """
    k = (key or "").strip()
    if not k:
        return {"success": False, "error": "key must be non-empty"}
    try:
        info = get_browser_session().press(k, selector=selector or "")
    except BrowserSessionError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return {"success": True, "key": k, **_page_dict(info)}


@registry.tool()
def browser_wait(
    selector: str = "",
    url_contains: str = "",
    timeout_ms: int = 15000,
) -> dict:
    """Wait for a selector to be visible and/or URL to contain a substring.

    :param selector: Optional Playwright selector.
    :param url_contains: Optional URL substring.
    :param timeout_ms: Max wait (default 15000).
    """
    if not (selector or "").strip() and not (url_contains or "").strip():
        return {
            "success": False,
            "error": "Provide selector and/or url_contains",
        }
    try:
        info = get_browser_session().wait(
            selector=selector or "",
            url_contains=url_contains or "",
            timeout_ms=int(timeout_ms or 15_000),
        )
    except BrowserSessionError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)
    return {"success": True, **_page_dict(info)}


@registry.tool()
def browser_screenshot(path: str = "", analyze: bool = False, question: str = "") -> dict:
    """Take a PNG screenshot of the current page.

    Saves under .sun/screenshots/ by default (or path). If analyze=true,
    also runs analyze_image on the screenshot (vision model).

    :param path: Optional output path (workspace-relative ok).
    :param analyze: If true, describe/answer via analyze_image.
    :param question: Question for vision when analyze=true.
    """
    try:
        session = get_browser_session()
        if not session.open:
            return {"success": False, "error": "No browser page open; call browser_open first"}
        out_path: Path | None = None
        if (path or "").strip():
            resolved, err = resolve_in_workspace(path.strip())
            if err or resolved is None:
                return {"success": False, "error": err or "invalid path"}
            out_path = resolved
        shot = session.screenshot(out_path)
        info = session.info()
    except BrowserSessionError as exc:
        return _err(exc)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)

    result: dict[str, Any] = {
        "success": True,
        "path": str(shot),
        **(_page_dict(info) if info else {}),
    }
    if analyze:
        from .builtins import analyze_image

        vision = analyze_image(
            path=str(shot),
            question=question
            or "描述这个页面：是否已登录、有哪些关键按钮/错误提示。",
        )
        result["analysis"] = vision
    return result


@registry.tool()
def browser_close() -> dict:
    """Close the Sun-managed browser session."""
    close_browser()
    return {"success": True, "closed": True}


def reset_browser_state() -> None:
    """Called from reset_planning_state / session clear."""
    close_browser()
