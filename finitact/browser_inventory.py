"""Read-only view of a kept browser tab for the outer agent (E2E-I21).

Without it the outer agent could not see a page state such as "No results" and repeated the same search. Text is the
default because it is cheap and exact; a screenshot is added only on request (yo-xe 2026-09-26). Everything returned
is untrusted page content.
"""

from __future__ import annotations

import base64
import time
import unicodedata
from typing import Any, Callable, Mapping, Sequence

OBSERVE_MAX_ITEMS = 200
OBSERVE_MAX_LINES = 200


WITHHELD = "withheld: read this text first; ask again with screenshot true only if it does not explain the state"


class ScreenshotGate:
    """Text first, enforced (yo-xe 2026-09-26): an image only for a state already read as text, or a sparse one.

    The outer agent asked for 15 images in 25 observations of E2E-02 although told to read text first.
    """

    def __init__(self) -> None:
        self._read: dict[str, str] = {}

    def allows(self, target: str, state: str, wanted: bool, sparse: bool) -> bool:
        allowed = wanted and (sparse or self._read.get(target) == state)
        self._read[target] = state
        return allowed


def observe_tab(
    tab_id: str,
    contains: str | None,
    screenshot: bool,
    *,
    browser_factory: Callable[..., Any],
    gate: ScreenshotGate | None = None,
) -> dict:
    browser = browser_factory(None, tab_id=tab_id)
    try:
        page = browser.observe(screenshot=False)
        view = page_view(page, contains)
        lines = [line for line in str(page.get("text", "")).splitlines() if line.strip()]
        # Text cannot describe a canvas, and a page with almost no text has little else to read.
        sparse = bool((page.get("unsupported") or {}).get("canvases")) or len(lines) < 3
        allowed = screenshot if gate is None else gate.allows(tab_id, page.get("fingerprint", ""), screenshot, sparse)
        if allowed:
            # A background tab paints no frames, so captureScreenshot timed out until the tab was brought forward.
            browser.call("Page.bringToFront")
            time.sleep(0.3)
            shot = browser.observe(screenshot=True)
            # A JavaScript dialog blocks captureScreenshot, so its observation carries no image (ADR-0047).
            if "screenshot" in shot:
                view["screenshot_jpeg"] = base64.b64decode(shot["screenshot"])
        elif screenshot:
            view["screenshot"] = WITHHELD
    finally:
        browser.close()
    return view


def show_tab(tab_id: str, *, browser_factory: Callable[..., Any], settle_s: float = 0.3) -> None:
    browser = browser_factory(None, tab_id=tab_id)
    try:
        browser.call("Page.bringToFront")
    finally:
        browser.close()
    time.sleep(settle_s)  # the window title follows the active tab a moment later


def page_view(page: Mapping[str, Any], contains: str | None) -> dict:
    needle = _folded(contains) if contains else None
    items = []
    for action in page.get("actions", ()):
        if action.get("kind") in ("wait",):
            continue
        item = {"kind": action["kind"], "label": action["label"]}
        for key in ("offscreen", "invalid", "value", "checked", "selected", "expanded"):
            if action.get(key) not in (None, ""):
                item[key] = action[key]
        if needle is None or needle in _folded(item["label"]) or needle in _folded(str(item.get("invalid", ""))):
            items.append(item)
    lines = [line for line in str(page.get("text", "")).splitlines() if line.strip()]
    matched = [line for line in lines if needle is None or needle in _folded(line)]
    view = {
        "url": page.get("url"),
        "title": page.get("title"),
        "items": items[:OBSERVE_MAX_ITEMS],
        "text": matched[:OBSERVE_MAX_LINES],
        "truncated": len(items) > OBSERVE_MAX_ITEMS or len(matched) > OBSERVE_MAX_LINES,
        **({"out_of_reach": reach} if (reach := out_of_reach(page)) else {}),
    }
    return view


# snapshot.js counts these but offers nothing inside them; the screen path reaches them (ADR-0048).
OUT_OF_REACH_KEYS = ("frames", "open_shadow_roots", "popup_links")
BROWSER_PROCESSES = frozenset({"chrome.exe", "msedge.exe", "brave.exe"})


def out_of_reach(page: Mapping[str, Any]) -> dict[str, int]:
    unsupported = page.get("unsupported") or {}
    return {key: count for key in OUT_OF_REACH_KEYS if (count := int(unsupported.get(key) or 0))}


def screen_target(title: str | None, windows: Sequence[Mapping[str, Any]]) -> str | None:
    """The one browser window whose title shows this page, as a run_windows target (ADR-0048).

    A window's title names only its active tab, so a background tab or two same-titled windows give no target.
    """

    if not title or not title.strip():
        return None
    matches = [
        item
        for item in windows
        if str(item.get("process", "")).casefold() in BROWSER_PROCESSES and str(item.get("title", "")).startswith(title)
    ]
    if len(matches) != 1:
        return None
    return f"window:{matches[0]['hwnd']}:{matches[0]['pid']}"


FINAL_TEXT_LINES = 40
FINAL_TEXT_CHARS = 2000
FINAL_FIELDS = 40
FINAL_FIELD_CHARS = 120
CHECK_MARKS = {"true": "x", "false": " ", "mixed": "-"}


def final_state(page: Mapping[str, Any], downloads: Sequence[Mapping[str, Any]] = ()) -> dict:
    """What a run ends on, returned with its result so the outer agent needs no extra observe (E2E-I28)."""

    lines, used = [], 0
    for line in (line.strip() for line in str(page.get("text", "")).splitlines()):
        if not line:
            continue
        if len(lines) >= FINAL_TEXT_LINES or used + len(line) > FINAL_TEXT_CHARS:
            break
        lines.append(line)
        used += len(line)
    invalid = [
        {"label": action["label"], "invalid": action["invalid"], **({"value": action["value"]} if action.get("value") else {})}
        for action in page.get("actions", ())
        if action.get("invalid") and action.get("kind") == "fill"
    ]
    dialogs = [str(text) for text in page.get("dialogs") or () if str(text).strip()]
    return {
        "url": page.get("url"),
        "title": page.get("title"),
        # An open dialog is what the page is asking now (E2E-I29), so it comes before the page text.
        **({"open_dialogs": dialogs} if dialogs else {}),
        **({"invalid_fields": invalid} if invalid else {}),
        # E2E-I32: the only trace of a confirmed export; without it the outer agent downloaded twice.
        **({"downloads": [dict(item) for item in downloads]} if downloads else {}),
        **({"fields": fields} if (fields := form_fields(page)) else {}),
        **({"out_of_reach": reach} if (reach := out_of_reach(page)) else {}),
        "text": lines,
    }


def form_fields(page: Mapping[str, Any]) -> list[str]:
    """Current form values as ``label = value`` / ``[x] label`` lines (E2E-I28).

    The first text lines of a form page are its header, so without these the outer agent observed the page
    after each run just to read what the fields hold.
    """

    fields, seen = [], set()
    for action in page.get("actions", ()):
        kind, label = action.get("kind"), str(action.get("label", ""))
        if action.get("invalid"):
            continue  # already in invalid_fields
        if kind == "select":
            label = label.rsplit(" → ", 1)[0]
            line = f"{label} = {action.get('current_value') or ''}"
        elif kind == "fill":
            line = f"{label} = {action.get('value') or ''}"
        elif kind == "click" and action.get("checked") in CHECK_MARKS:
            line = f"[{CHECK_MARKS[action['checked']]}] {label}"
        else:
            continue
        if label in seen:
            continue
        seen.add(label)
        fields.append(line[:FINAL_FIELD_CHARS])
        if len(fields) >= FINAL_FIELDS:
            break
    return fields


def until_unmet(until, page: Mapping[str, Any], new_downloads: Sequence[Mapping[str, Any]] | None) -> list[str]:
    """The parts of an until (runs.Until) this page does not show; empty when it is reached (EXP-0011).

    ``new_downloads`` are those begun since the goal started; None when the browser reports no download events.
    """

    missing = []
    if until.download:
        if new_downloads is None:
            missing.append("download completed (this browser reports no downloads)")
        elif not any(item.get("state") == "completed" for item in new_downloads):
            missing.append("download completed")
    if until.field is not None:
        wanted = _folded(until.value)
        label = _folded(until.field)
        values = [
            _folded(str(action.get("current_value") if action.get("kind") == "select" else action.get("value") or ""))
            for action in page.get("actions", ())
            if action.get("kind") in ("select", "fill")
            and label in _folded(str(action.get("label", "")).rsplit(" → ", 1)[0])
        ]
        if wanted not in values:
            missing.append(f"{until.field} = {until.value}")
    if until.text is not None:
        shown = _folded(" ".join([str(page.get("text", "")), *map(str, page.get("dialogs") or ())]))
        if _folded(until.text) not in shown:
            missing.append(f"text {until.text!r} shown")
    return missing


def _folded(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())
