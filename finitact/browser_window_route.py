"""Conservative, read-only browser tab lookup for an isolated Windows browser."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from .browser_inventory import BROWSER_PROCESSES


@dataclass(frozen=True)
class BrowserWindowRoute:
    tab_id: str
    origin: str


# Frames, shadow roots and popup links are not gated: run_browser itself ignores them and reports its own
# out_of_reach in final_state (ADR-0051 追記1).
_PAGE_PROBE = """(() => ({title: document.title, url: location.href, visibility: document.visibilityState}))()"""


class BrowserCDP:
    """Browser-level CDP socket. The harness daemon sends SystemInfo/Browser calls on its page session, where
    Chrome rejects them, and starting it may create or attach a tab, which would break the singleton premise."""

    def __init__(self, ws_url: str):
        from websockets.sync.client import connect

        self.ws = connect(ws_url, open_timeout=5, close_timeout=1, max_size=None)
        self.next_id = 0

    def __call__(self, method: str, session_id: str | None = None, **params) -> dict:
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params}
        if session_id:
            message["sessionId"] = session_id
        self.ws.send(json.dumps(message))
        while True:
            response = json.loads(self.ws.recv(timeout=8))
            if response.get("id") != self.next_id:
                continue
            if "error" in response:
                raise RuntimeError(f"{method}: {response['error'].get('message')}")
            return response.get("result") or {}

    def close(self) -> None:
        self.ws.close()


@contextmanager
def browser_cdp():
    with urllib.request.urlopen(f"{os.environ['BU_CDP_URL'].rstrip('/')}/json/version", timeout=3) as response:
        version = json.load(response)
    cdp = BrowserCDP(version["webSocketDebuggerUrl"])
    try:
        yield cdp
    finally:
        cdp.close()


def _local_endpoint() -> bool:
    url = os.environ.get("BU_CDP_URL")
    ws = os.environ.get("BU_CDP_WS")
    if not url or ws:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")


def _reject(reason: str) -> None:
    # The caller falls back to the screen path silently; this line is the only evidence of which premise failed.
    print(f"browser route: screen ({reason})", file=sys.stderr)
    return None


def find_singleton_tab(
    target_id: str,
    windows: Sequence[Mapping[str, Any]],
    *,
    cdp_call: Callable[..., dict],
    window_probe: Callable[[], Sequence[Mapping[str, Any]]],
) -> BrowserWindowRoute | None:
    """Find the only visible page in the only local browser window; ambiguity stays on screen."""

    if not _local_endpoint():
        return _reject('no local CDP endpoint')
    parts = target_id.split(":")
    if len(parts) != 3 or parts[0] != "window" or not all(part.isdecimal() for part in parts[1:]):
        return _reject('target is not window:<hwnd>:<pid>')
    hwnd, pid = map(int, parts[1:])

    def owned_by(listing):
        # The target's own bubbles (download, permission) sit outside the page and do not block CDP input.
        return [
            window for window in listing
            if int(window.get("pid", -1)) == pid and str(window.get("process", "")).casefold() in BROWSER_PROCESSES
            and window.get("popup_of") != hwnd
        ]

    owned = owned_by(windows)
    if len(owned) != 1 or int(owned[0].get("hwnd", -1)) != hwnd or owned[0].get("minimized"):
        detail = "; ".join(
            f"{w.get('hwnd')} {w.get('class_name')!r} {str(w.get('title', ''))[:60]!r}"
            f"{' minimized' if w.get('minimized') else ''} {w.get('rect')}"
            for w in owned
        )
        return _reject(f"owned windows {len(owned)}: {detail}")
    browser_processes = [
        item for item in cdp_call("SystemInfo.getProcessInfo").get("processInfo", ())
        if str(item.get("type", "")).casefold() == "browser"
    ]
    if len(browser_processes) != 1 or browser_processes[0].get("id") != pid:
        return _reject('browser process mismatch')
    pages = [
        item for item in cdp_call("Target.getTargets").get("targetInfos", ())
        if item.get("type") == "page"
    ]
    if len(pages) != 1:
        return _reject(f"page targets {len(pages)}")
    page = pages[0]
    tab_id = page.get("targetId")
    title = page.get("title")
    if not tab_id or not title or not str(owned[0].get("title", "")).startswith(title):
        return _reject('page title does not prefix window title')
    if not cdp_call("Browser.getWindowForTarget", targetId=tab_id).get("windowId"):
        return _reject('no CDP window for target')
    session = cdp_call("Target.attachToTarget", targetId=tab_id, flatten=True).get("sessionId")
    if not session:
        return _reject('attach failed')
    try:
        response = cdp_call(
            "Runtime.evaluate", session_id=session, expression=_PAGE_PROBE,
            returnByValue=True, throwOnSideEffect=True,
        )
    finally:
        cdp_call("Target.detachFromTarget", sessionId=session)
    state = (response.get("result") or {}).get("value") or {}
    if response.get("exceptionDetails") or not isinstance(state, dict):
        return _reject('probe exception')
    url = state.get("url")
    parsed = urlparse(url) if isinstance(url, str) else None
    if (
        not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc
        or state.get("title") != title or state.get("visibility") != "visible"
        or page.get("url") != url
    ):
        return _reject('page state mismatch')
    latest = owned_by(window_probe())
    if len(latest) != 1 or int(latest[0].get("hwnd", -1)) != hwnd or latest[0].get("title") != owned[0].get("title"):
        return _reject('window changed during probe')
    return BrowserWindowRoute(tab_id, f"{parsed.scheme}://{parsed.netloc}")
