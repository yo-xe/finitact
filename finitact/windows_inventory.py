"""Read-only Windows inventory for the outer agent (E2E-I4, E2E-I9).

``list_windows`` finds the HWND/PID a ``run_windows`` target needs; ``observe_window`` reads a target's
labels without any input, lease or indicator, so checking a result no longer costs a mutating run.
Titles and labels are untrusted screen text.
"""

from __future__ import annotations

import ctypes
import os
import unicodedata
from ctypes import wintypes
from typing import Any, Callable, Mapping, Sequence

from .action_adapter import ActionAdapter
from .contracts import EDGE_CONTOUR_SOURCE
from .runs import Goal, WindowsRunRequest

# Candidate labels are OCR text; beyond this the caller should narrow with ``contains``.
OBSERVE_MAX_ITEMS = 200
TEXT_INPUT_CONTROL_TYPES = ("ControlType.Edit", "ControlType.ComboBox", "ControlType.Document")
# BUG-0041: the outer agent runs in a terminal; acting on one can minimize or type into its own session.
# FINITACT_PROTECTED_PROCESSES replaces the list (comma-separated; empty allows every window).
DEFAULT_PROTECTED_PROCESSES = "WindowsTerminal.exe,OpenConsole.exe,conhost.exe"


def protected_processes() -> frozenset[str]:
    names = os.environ.get("FINITACT_PROTECTED_PROCESSES", DEFAULT_PROTECTED_PROCESSES)
    return frozenset(name.strip().casefold() for name in names.split(",") if name.strip())


def protected_reason(process: str, protected: frozenset[str] | None = None) -> str | None:
    if process.casefold() in (protected_processes() if protected is None else protected):
        return f"target is protected: {process} may host the caller's own session (FINITACT_PROTECTED_PROCESSES)"
    return None


def target_guard(target_id: str) -> str | None:
    """Refuses a protected window, judged by the HWND's real owner rather than the PID the caller wrote."""

    parts = target_id.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    root = user32.GetAncestor(wintypes.HWND(int(parts[1])), 2) or int(parts[1])  # GA_ROOT
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(root), ctypes.byref(pid))
    return protected_reason(_process_name(kernel32, pid.value))


def list_windows(title_contains: str | None = None) -> list[dict[str, Any]]:
    """Visible top-level windows, front to back, plus the desktop (Progman) and taskbar."""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    foreground = user32.GetForegroundWindow()
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = (wintypes.HWND, ctypes.c_uint)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    dwmapi = ctypes.WinDLL("dwmapi")
    found: list[dict[str, Any]] = []

    def cloaked(hwnd) -> bool:
        value = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(value), 4)  # DWMWA_CLOAKED
        return bool(value.value)

    def visit(hwnd) -> None:
        # Closed Start/Search/input surfaces stay "visible" but cloaked (BUG-0047).
        if not user32.IsWindowVisible(hwnd) or cloaked(hwnd):
            return
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, 256)
        # Untitled visible windows are tool/IME/host surfaces; the desktop and taskbar are the exceptions
        # a scenario starts from.
        if not title.value and class_name.value not in ("Progman", "Shell_TrayWnd"):
            return
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        item = {
            "hwnd": int(hwnd),
            "pid": pid.value,
            "title": title.value,
            "class_name": class_name.value,
            "process": _process_name(kernel32, pid.value),
            "rect": [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top],
            "minimized": bool(user32.IsIconic(hwnd)),
            "foreground": int(hwnd) == int(foreground or 0),
        }
        owner = user32.GetWindow(hwnd, 4)  # GW_OWNER
        # Owned tool windows are the owner's bubbles (e.g. Chrome's download bubble), not modal dialogs, which
        # lack WS_EX_TOOLWINDOW; browser routing needs to tell them apart.
        if owner and user32.GetWindowLongPtrW(hwnd, -20) & 0x80:  # GWL_EXSTYLE, WS_EX_TOOLWINDOW
            item["popup_of"] = int(owner)
        found.append(item)

    # BUG-0047: once Start/Search has opened, EnumWindows stops returning the taskbar and the search popup;
    # FindWindowEx walks the same z-order without that filter.
    hwnd = None
    while hwnd := user32.FindWindowExW(None, hwnd, None, None):
        visit(hwnd)
    protected = protected_processes()
    for item in found:
        if protected_reason(item["process"], protected):
            item["protected"] = True
    return filter_windows(found, title_contains)


# BUG-0048: open Start is two windows: SearchHost holds the foreground and keys while the visible Start panel,
# search box included, is StartMenuExperienceHost's and takes the pointer hit.
SHELL_SURFACE_PROCESSES = frozenset({"startmenuexperiencehost.exe", "searchhost.exe"})


def _is_shell_surface(process: str, class_name: str) -> bool:
    return (
        process.casefold() in SHELL_SURFACE_PROCESSES and class_name == "Windows.UI.Core.CoreWindow"
    ) or (process.casefold() == "explorer.exe" and class_name == "Shell_TrayWnd")


def shell_surface_peers(hwnd: int) -> tuple[int, ...]:
    """The other visible Start/Search/taskbar surfaces when ``hwnd`` is one of them."""

    return _shell_surfaces(hwnd, hidden=False)


# BUG-0057: dismissed Search stays foreground while cloaked or hidden, also over the app it just launched. It cannot
# take a pointer hit or show input, so it may only excuse the foreground check of a pointer gesture.
def hidden_shell_surfaces(hwnd: int) -> tuple[int, ...]:
    """Cloaked or hidden Start/Search surfaces, whatever ``hwnd`` is."""

    return _shell_surfaces(hwnd, hidden=True)


def _shell_surfaces(hwnd: int, *, hidden: bool) -> tuple[int, ...]:

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
    dwmapi = ctypes.WinDLL("dwmapi")

    def surface(handle, want_hidden: bool = False) -> bool:
        cloak = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(handle, 14, ctypes.byref(cloak), 4)  # DWMWA_CLOAKED
        shown = bool(user32.IsWindowVisible(handle)) and not cloak.value
        if shown == want_hidden:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, class_name, 256)
        return _is_shell_surface(_process_name(kernel32, pid.value), class_name.value)

    if not hidden and not surface(wintypes.HWND(hwnd)):
        return ()
    peers = []
    handle = None
    while handle := user32.FindWindowExW(None, handle, None, None):
        if int(handle) != hwnd and surface(handle, hidden):
            peers.append(int(handle))
    return tuple(peers)


def filter_windows(windows: Sequence[dict[str, Any]], title_contains: str | None) -> list[dict[str, Any]]:
    if not title_contains:
        return list(windows)
    needle = _folded(title_contains)
    return [
        item
        for item in windows
        if any(needle in _folded(item.get(key, "")) for key in ("title", "process", "class_name"))
    ]


TARGET_PREFIXES = ("window:", "uia:", "screen:")


def resolve_window(query: str, windows: Sequence[dict[str, Any]]) -> str:
    """``window:<HWND>:<PID>`` of the one unprotected window titled ``query``.

    The outer agent knows the window by its title; a list_windows round trip only to copy the HWND cost it a turn
    and about 5k tokens per task (public-repo plan u1). An exact title wins over partial matches; anything still
    ambiguous is refused with the candidates, never guessed.
    """

    if query.startswith(TARGET_PREFIXES):
        return query
    needle = _folded(query)
    usable = [item for item in windows if not item.get("protected")]
    exact = [item for item in usable if _folded(item.get("title", "")) == needle]
    matches = exact or [item for item in usable if needle and needle in _folded(item.get("title", ""))]
    shown = [item for item in matches if not item.get("minimized")]
    if len(matches) > 1 and len(shown) == 1:
        # BUG-0065: a minimized namesake is not the window on screen; the outer agent then always chose the visible one.
        matches = shown
    if len(matches) == 1:
        return f"window:{matches[0]['hwnd']}:{matches[0]['pid']}"
    if not matches:
        raise ValueError(f"no unprotected window titled {query!r}; call list_windows")
    listed = "; ".join(f"{item['title']!r} window:{item['hwnd']}:{item['pid']}" for item in matches[:8])
    raise ValueError(f"{len(matches)} windows match {query!r}: {listed}; pass one target_id")


def _folded(text: str) -> str:
    # OCR spaces out characters ("f i n i t a c t"), so matching ignores whitespace as well as case and width.
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _process_name(kernel32, pid: int) -> str:
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)


def observe_window(
    target_id: str,
    contains: str | None = None,
    *,
    adapter_factory: Callable[[WindowsRunRequest], ActionAdapter],
    read_caret: Callable[[int], tuple[int, int, int, int] | None] | None = None,
    screenshot: bool = False,
    gate=None,
    window_origin: Callable[[set[int]], dict[int, tuple[int, int]]] | None = None,
    keep: Callable[[str, Any, Any, dict[str, dict[str, str]]], None] | None = None,
) -> dict[str, Any]:
    """The target's current labels, as a run would see them, without input.

    With ``keep``, each item carries a ref and the observation is handed over so that a run can act on one item
    without the chooser (ADR-0041, ADR-0043).
    """

    request = WindowsRunRequest(
        run_id="observe-window",
        target_id=target_id,
        goals=(Goal("observe", "observe"),),
        # set_range shows a slider and its number (BUG-0056); nothing is delivered here.
        allowed_operations=("click", "fill", "set_range"),
        deadline_ms=30_000,
    )
    adapter = adapter_factory(request)
    try:
        observation = adapter.observe()
        # The frame the labels came from, so the picture and the text describe the same moment.
        retain = getattr(adapter, "retain", None)
        retained = retain(observation) if retain is not None and (screenshot or keep is not None) else None
    finally:
        adapter.close()
    root_hwnd = int(target_id.split(":")[1])
    caret = read_caret(root_hwnd) if read_caret and target_id.startswith("screen:") else None
    popups = {int(c.attributes["scope_hwnd"]) for c in observation.candidates if c.attributes.get("scope") == "owned_popup"}
    origins = (window_origin or _window_origins)({root_hwnd, *popups}) if popups else {}
    view = observation_items(
        observation.candidates,
        contains,
        caret=caret,
        values=observation.untrusted_values,
        root_hwnd=root_hwnd,
        origins=origins,
        with_refs=keep is not None and retained is not None,
    )
    refs = view.pop("refs", None)
    if refs:
        keep(target_id, observation, retained, refs)
    # Text first (browser_inventory.ScreenshotGate): the image only for a state already read, or a nearly empty one.
    allowed = screenshot if gate is None else gate.allows(target_id, observation.observation_id, screenshot, view["total"] < 3)
    if allowed and retained is not None and retained.frames:
        view["screenshot_png"] = frame_png(retained.frames[0])
    elif screenshot:
        from .browser_inventory import WITHHELD

        view["screenshot"] = WITHHELD
    return view


def frame_png(frame) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.frombuffer("RGBA", (frame.width, frame.height), frame.pixels, "raw", "BGRA", 0, 1).convert("RGB").save(
        buffer, "PNG", optimize=False
    )
    return buffer.getvalue()


def observation_items(
    candidates,
    contains: str | None,
    *,
    caret: tuple[int, int, int, int] | None = None,
    values: Mapping[str, str] | None = None,
    root_hwnd: int | None = None,
    origins: Mapping[int, tuple[int, int]] | None = None,
    with_refs: bool = False,
) -> dict[str, Any]:
    by_key: dict[tuple[str, tuple], dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    # Item ref -> {operation: candidate id}: an item merges the candidates offered on one control (E2E-I35).
    refs: dict[str, dict[str, str]] = {}
    box = _caret_box(candidates, caret) if caret is not None else None
    # E2E-I39: text inside a field known to be empty is its placeholder, not unsent input.
    empty_fields = [
        rect
        for candidate in candidates
        if _field_value(candidate, values) == "" and len(rect := tuple(candidate.attributes.get("rect") or ())) == 4
    ]
    for candidate in candidates:
        label = str(candidate.label)
        rect = tuple(candidate.attributes.get("rect") or ())
        value = _field_value(candidate, values)
        popup = _popup_hwnd(candidate, root_hwnd)
        if popup is not None:
            rect = _in_root(rect, popup, root_hwnd, origins or {}) if candidate.attributes.get("scope") else rect
        # Shape-only edge regions ("boxed_region_22x27") carry no text to check a result against.
        if not label.strip() or candidate.attributes.get("source") == EDGE_CONTOUR_SOURCE:
            continue
        if (label, rect) in by_key:
            # The same control offered for several operations; its fill carries the value (E2E-I35).
            if value is not None:
                by_key[(label, rect)]["value"] = value
            refs[str(items.index(by_key[(label, rect)]) + 1)].setdefault(candidate.operation, candidate.id)
            continue
        ref = str(len(items) + 1)
        item: dict[str, Any] = {"ref": ref, "label": label, "rect": list(rect)} if with_refs else {"label": label, "rect": list(rect)}
        refs[ref] = {candidate.operation: candidate.id}
        if value is not None:
            item["value"] = value
        if popup is not None:
            # E2E-03: a suggestion list read as loose text at popup-relative coordinates; the agent asked for an
            # image to learn a dropdown was open.
            item["popup"] = True
        elif candidate.attributes.get("offscreen"):
            # A scrolled-out UIA element carries the whole window as a placeholder rect (windows_uia_reader);
            # on the caret test it marked the whole message history as unsent (E2E-I31).
            item["offscreen"] = True
        elif value is None and len(rect) == 4 and any(_inside(rect, field) for field in empty_fields):
            item["placeholder"] = True
        elif caret is not None and len(rect) == 4 and _on_caret_line(rect, caret, box):
            # E2E-01: typed text still in the message box read like a posted message to the outer agent.
            item["in_focused_input"] = True
        by_key[(label, rect)] = item
        items.append(item)
    matching = [item for item in items if _folded(contains) in _folded(item["label"])] if contains else items
    view = {
        "total": len(items),
        "matched": len(matching),
        "items": matching[:OBSERVE_MAX_ITEMS],
        "truncated": len(matching) > OBSERVE_MAX_ITEMS,
    }
    return {**view, "refs": refs} if with_refs else view


def _popup_hwnd(candidate, root_hwnd: int | None) -> int | None:
    """The owned popup window a candidate lies in, or None for the target window itself."""

    scope = candidate.attributes.get("scope_hwnd")
    if scope is None or root_hwnd is None or int(scope) == root_hwnd:
        return None
    return int(scope)


def _in_root(rect: tuple, popup: int, root_hwnd: int | None, origins: Mapping[int, tuple[int, int]]) -> tuple:
    """A screen-path rect is relative to its own popup frame; shown relative to the target window like the rest."""

    if len(rect) != 4 or popup not in origins or root_hwnd not in origins:
        return rect
    dx, dy = origins[popup][0] - origins[root_hwnd][0], origins[popup][1] - origins[root_hwnd][1]
    return (rect[0] + dx, rect[1] + dy, rect[2], rect[3])


def _window_origins(hwnds: set[int]) -> dict[int, tuple[int, int]]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    # Physical pixels, the context the captured frames and their rects use (BUG-0017).
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        origins = {}
        for hwnd in hwnds:
            rect = wintypes.RECT()
            if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                origins[hwnd] = (rect.left, rect.top)
        return origins
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def _field_value(candidate, values: Mapping[str, str] | None) -> str | None:
    """A text input's current value (untrusted), or None for anything else or when UIA did not read it."""

    if candidate.operation == "set_range":
        return candidate.binding.get("field_value")
    if candidate.operation != "fill":
        return None
    # E2E-03: Chromium's autofill list has a writable, empty ValuePattern; its suggestion read as a placeholder.
    if candidate.attributes.get("control_type") not in (None, *TEXT_INPUT_CONTROL_TYPES):
        return None
    if "field_value" in candidate.binding:
        return candidate.binding["field_value"]
    return (values or {}).get(candidate.id)


def _inside(rect: tuple, field: tuple) -> bool:
    return (
        rect != field
        and field[0] <= rect[0]
        and field[1] <= rect[1]
        and rect[0] + rect[2] <= field[0] + field[2]
        and rect[1] + rect[3] <= field[1] + field[3]
    )


def _caret_box(candidates, caret: tuple[int, int, int, int]) -> tuple | None:
    """The smallest on-screen rect holding the caret: the input box itself when UIA reports it."""

    caret_x, caret_y, _caret_width, caret_height = caret
    point = (caret_x, caret_y + caret_height // 2)
    holding = [
        (candidate.operation != "fill", rect[2] * rect[3], rect)
        for candidate in candidates
        if not candidate.attributes.get("offscreen")
        and len(rect := tuple(candidate.attributes.get("rect") or ())) == 4
        and _holds(rect, point)
    ]
    # The typed text holds the caret too; the fill target around it is the box.
    return min(holding)[2] if holding else None


def _holds(rect: tuple, point: tuple[int, int]) -> bool:
    x, y, width, height = rect
    return x <= point[0] <= x + width and y <= point[1] <= y + height


def _on_caret_line(rect: tuple, caret: tuple[int, int, int, int], box: tuple | None = None) -> bool:
    x, y, width, height = rect
    caret_x, caret_y, _caret_width, caret_height = caret
    same_line = y < caret_y + caret_height and caret_y < y + height
    if box is not None:
        # E2E-I31: with the caret at the start of an empty box, the slack below reached the buttons beside it.
        return same_line and box[0] - 2 <= x and x + width <= box[0] + box[2] + 2
    # The caret sits after the typed text (or at its start); a few characters of slack either side.
    return same_line and x - 2 * height <= caret_x <= x + width + 2 * height
