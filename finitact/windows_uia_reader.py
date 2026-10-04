"""COM UI Automation reader for screen-path candidates (ADR-0036).

The managed System.Windows.Automation client showed no desktop icons where COM IUIAutomation listed all 32 in
about 20 ms, so this talks COM through comtypes. All COM objects live on one dedicated MTA thread; callers get plain
values with a deadline, and a late answer is dropped with its call (consult 20260925-2134 point 6).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass

# ControlType ids (UIAutomationClient.h) whose element is a pointer target in its own right.
CLICK_ROLES = {
    50000: "button", 50002: "checkbox", 50003: "combobox", 50005: "link", 50007: "listitem", 50011: "menuitem",
    50013: "radio", 50019: "tab", 50024: "treeitem", 50031: "splitbutton", 50020: "text", 50004: "edit",
}  # fmt: skip
EDIT_ROLE = 50004
# BUG-0046: Win11 Notepad's body is a Document; only a writable one counts, not Chrome's read-only page.
DOCUMENT_ROLE = 50030
# BUG-0056: a slider takes a number through RangeValuePattern; aiming at its thumb by pixels never converged.
SLIDER_ROLE = 50015
# ADR-0038: scrolled-out items often have no rectangle to measure a distance by, so they are capped by count.
MAX_OFFSCREEN = 40
# Provider-side cap; the caller's own deadline still decides (UIA's default is 20 s).
TRANSACTION_TIMEOUT_MS = 2_000


class UiaUnavailable(RuntimeError):
    """No usable answer in time; the caller falls back to OCR before any input."""


@dataclass(frozen=True)
class UiaElement:
    role: str
    name: str
    # Window-relative physical pixels, like WindowFrame and OCR regions.
    rect: tuple[int, int, int, int]
    # Editable evidence: an Edit, or a ValuePattern that is not read-only (point 4 of the consult).
    editable: bool
    value: str | None
    # ADR-0038: scrolled out of its list's view but able to scroll itself in (ScrollItemPattern).
    offscreen: bool = False
    # The provider says the value is not valid for its form (UIA IsDataValidForForm false).
    invalid: bool = False
    # A settable slider's (minimum, maximum); its current number is ``value``.
    range: tuple[float, float] | None = None
    # ADR-0039: "true"/"false"/"mixed" from TogglePattern, else SelectionItemPattern's IsSelected.
    checked: str | None = None
    # A click selects this item (SelectionItemPattern) rather than flipping it (TogglePattern).
    selects: bool = False


class ComUiaReader:
    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="finitact-uia", initializer=self._init)
        self._local = threading.local()

    def read(self, hwnd: int, *, timeout_s: float = 1.5) -> tuple[UiaElement, ...]:
        future = self._pool.submit(self._read, hwnd)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            # The worker may still be inside a hung provider; its eventual answer is never read.
            raise UiaUnavailable(f"UIA read of {hwnd} exceeded {timeout_s}s") from exc
        except UiaUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any COM failure means "use OCR", never "act on a guess"
            raise UiaUnavailable(f"UIA read of {hwnd} failed: {type(exc).__name__}") from exc

    def _init(self) -> None:
        import ctypes
        import sys

        # comtypes initializes COM in whichever thread imports it first, as STA unless told otherwise; a later
        # MTA CoInitializeEx then fails with RPC_E_CHANGED_MODE.
        sys.coinit_flags = 0
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialized as MTA by the import above
        # BUG-0017: physical pixels, the same context the PrintWindow capture uses.
        ctypes.WinDLL("user32").SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        automation = comtypes.client.CreateObject(U.CUIAutomation8, interface=U.IUIAutomation)
        try:
            automation.QueryInterface(U.IUIAutomation2).TransactionTimeout = TRANSACTION_TIMEOUT_MS
        except Exception:  # noqa: BLE001 - older UIA without IUIAutomation2 keeps its default
            pass
        request = automation.CreateCacheRequest()
        for prop in (
            U.UIA_NamePropertyId, U.UIA_ControlTypePropertyId, U.UIA_BoundingRectanglePropertyId,
            U.UIA_IsOffscreenPropertyId, U.UIA_IsEnabledPropertyId, U.UIA_IsValuePatternAvailablePropertyId,
            U.UIA_ValueIsReadOnlyPropertyId, U.UIA_ValueValuePropertyId, U.UIA_IsScrollItemPatternAvailablePropertyId,
            U.UIA_IsDataValidForFormPropertyId, U.UIA_RuntimeIdPropertyId, U.UIA_NativeWindowHandlePropertyId,
            U.UIA_IsRangeValuePatternAvailablePropertyId, U.UIA_RangeValueValuePropertyId,
            U.UIA_RangeValueIsReadOnlyPropertyId, U.UIA_RangeValueMinimumPropertyId, U.UIA_RangeValueMaximumPropertyId,
            U.UIA_IsTogglePatternAvailablePropertyId, U.UIA_ToggleToggleStatePropertyId,
            U.UIA_IsSelectionItemPatternAvailablePropertyId, U.UIA_SelectionItemIsSelectedPropertyId,
        ):  # fmt: skip
            request.AddProperty(prop)
        self._local.parts = (U, automation, request, ctypes)

    def _read(self, hwnd: int) -> tuple[UiaElement, ...]:
        U, automation, request, ctypes = self._local.parts
        from ctypes import wintypes

        window = wintypes.RECT()
        if not ctypes.WinDLL("user32").GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(window)):
            raise UiaUnavailable("GetWindowRect failed")
        width, height = window.right - window.left, window.bottom - window.top
        root = automation.ElementFromHandle(hwnd)
        condition = automation.CreateTrueCondition()
        owned = _visible_owned_windows(ctypes, hwnd)
        if owned:
            found = _walk_once(U, root, condition, request, skip_windows=owned)
        else:
            array = root.FindAllBuildCache(U.TreeScope_Descendants, condition, request)
            found = [array.GetElement(index) for index in range(array.Length)]
        elements: list[UiaElement] = []
        hidden = 0
        for element in found:
            role = CLICK_ROLES.get(element.CachedControlType)
            if element.CachedControlType == DOCUMENT_ROLE and _writable(U, element):
                role = "document"
            limits = _settable_range(U, element) if element.CachedControlType == SLIDER_ROLE else None
            if limits is not None:
                role = "slider"
            offscreen = bool(element.CachedIsOffscreen)
            if role is None or not element.CachedIsEnabled:
                continue
            if offscreen and not element.GetCachedPropertyValue(U.UIA_IsScrollItemPatternAvailablePropertyId):
                continue
            box = element.CachedBoundingRectangle
            left, top = max(0, box.left - window.left), max(0, box.top - window.top)
            right, bottom = min(width, box.right - window.left), min(height, box.bottom - window.top)
            if offscreen:
                if hidden >= MAX_OFFSCREEN:
                    continue
                hidden += 1
                if right - left < 2 or bottom - top < 2:
                    # A scrolled-out list item often reports no rectangle; its place is read after the scroll.
                    left, top, right, bottom = 0, 0, width, height
            if right - left < 2 or bottom - top < 2:
                continue
            has_value = bool(element.GetCachedPropertyValue(U.UIA_IsValuePatternAvailablePropertyId))
            read_only = bool(element.GetCachedPropertyValue(U.UIA_ValueIsReadOnlyPropertyId)) if has_value else True
            # Discord's mute toggles expose a writable ValuePattern too; only inputs count.
            editable = role in ("edit", "document") or (role == "combobox" and has_value and not read_only)
            value = str(element.GetCachedPropertyValue(U.UIA_ValueValuePropertyId) or "") if has_value else None
            if limits is not None:
                value = _number(element.GetCachedPropertyValue(U.UIA_RangeValueValuePropertyId))
            name = (element.CachedName or "").strip()
            # A nameless element is still a target when it is editable (an empty message box).
            if not name and not editable:
                continue
            # Notepad's RichEdit Document reports False with no form behind it.
            invalid = editable and role != "document" and element.GetCachedPropertyValue(U.UIA_IsDataValidForFormPropertyId) is False
            checked, selects = _state(U, element)
            elements.append(
                UiaElement(
                    role, name, (left, top, right - left, bottom - top), editable, value, offscreen, invalid, limits,
                    checked, selects,
                )
            )
        return tuple(elements)

    def scroll_into_view(self, hwnd: int, role: str, name: str, *, timeout_s: float = 1.5) -> None:
        """Scroll the one element with ``role`` and ``name`` into view through its ScrollItemPattern."""

        future = self._pool.submit(self._scroll, hwnd, role, name)
        try:
            future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            raise UiaUnavailable(f"UIA scroll of {name!r} exceeded {timeout_s}s") from exc

    def _scroll(self, hwnd: int, role: str, name: str) -> None:
        U, automation, request, _ctypes = self._local.parts
        found = automation.ElementFromHandle(hwnd).FindAllBuildCache(
            U.TreeScope_Descendants, automation.CreateTrueCondition(), request
        )
        matches = [
            found.GetElement(index)
            for index in range(found.Length)
            if CLICK_ROLES.get(found.GetElement(index).CachedControlType) == role
            and (found.GetElement(index).CachedName or "").strip() == name
        ]
        if len(matches) != 1:
            raise UiaUnavailable(f"{len(matches)} elements named {name!r}")
        pattern = matches[0].GetCurrentPattern(U.UIA_ScrollItemPatternId).QueryInterface(U.IUIAutomationScrollItemPattern)
        pattern.ScrollIntoView()


    def set_range(self, hwnd: int, name: str, number: float, *, timeout_s: float = 1.5) -> bool:
        """Set the one slider named ``name`` to ``number``; True when it reads back that number."""

        future = self._pool.submit(self._set_range, hwnd, name, number)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            raise UiaUnavailable(f"UIA set_range of {name!r} exceeded {timeout_s}s") from exc

    def _set_range(self, hwnd: int, name: str, number: float) -> bool:
        U, automation, request, _ctypes = self._local.parts
        found = automation.ElementFromHandle(hwnd).FindAllBuildCache(
            U.TreeScope_Descendants, automation.CreateTrueCondition(), request
        )
        matches = [
            element
            for element in (found.GetElement(index) for index in range(found.Length))
            if element.CachedControlType == SLIDER_ROLE
            and (element.CachedName or "").strip() == name
            and _settable_range(U, element) is not None
        ]
        if len(matches) != 1:
            raise UiaUnavailable(f"{len(matches)} sliders named {name!r}")
        pattern = matches[0].GetCurrentPattern(U.UIA_RangeValuePatternId).QueryInterface(U.IUIAutomationRangeValuePattern)
        pattern.SetValue(number)
        # A slider with a step snaps to it; only a read-back at the asked number confirms the delivery.
        return abs(float(pattern.CurrentValue) - number) < 1e-6


def _settable_range(U, element) -> tuple[float, float] | None:
    if not element.GetCachedPropertyValue(U.UIA_IsRangeValuePatternAvailablePropertyId):
        return None
    if element.GetCachedPropertyValue(U.UIA_RangeValueIsReadOnlyPropertyId):
        return None
    return (
        float(element.GetCachedPropertyValue(U.UIA_RangeValueMinimumPropertyId)),
        float(element.GetCachedPropertyValue(U.UIA_RangeValueMaximumPropertyId)),
    )


def _state(U, element) -> tuple[str | None, bool]:
    if element.GetCachedPropertyValue(U.UIA_IsTogglePatternAvailablePropertyId):
        state = element.GetCachedPropertyValue(U.UIA_ToggleToggleStatePropertyId)
        return {0: "false", 1: "true", 2: "mixed"}.get(state), False
    if element.GetCachedPropertyValue(U.UIA_IsSelectionItemPatternAvailablePropertyId):
        return ("true" if element.GetCachedPropertyValue(U.UIA_SelectionItemIsSelectedPropertyId) else "false"), True
    return None, False


def _number(value) -> str:
    number = float(value or 0)
    return str(int(number)) if number.is_integer() else repr(number)


def _writable(U, element) -> bool:
    return bool(element.GetCachedPropertyValue(U.UIA_IsValuePatternAvailablePropertyId)) and not bool(
        element.GetCachedPropertyValue(U.UIA_ValueIsReadOnlyPropertyId)
    )


def _visible_owned_windows(ctypes, hwnd: int) -> frozenset[int]:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.GetWindow.restype = wintypes.HWND
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(candidate, _lparam):
        if user32.IsWindowVisible(candidate) and int(user32.GetWindow(candidate, 4) or 0) == hwnd:  # GW_OWNER
            found.append(int(candidate))
        return True

    user32.EnumWindows(visit, 0)
    return frozenset(found)


def _walk_once(U, root, condition, request, *, skip_windows: frozenset[int] = frozenset()) -> list:
    """Descendants visiting each element once.

    E2E-I38: with its autofill popup open, Brave's tree lists the window's own top-level panes again as
    children of a pane below them; FindAll followed that loop to about 6,700 elements in 1.9 s, past the
    read deadline, and the window fell back to OCR. Walking children skips what it has already seen
    (109 elements, 0.05 s) but costs about 1.5x FindAll on an acyclic tree, so it runs only while an owned
    popup is shown. The popup's own subtree shows up there too; it is read as its own frame, and a click
    aimed at it through the owner was refused as covered by the popup.
    """

    seen: set[tuple] = set()
    found: list = []
    pending = [root]
    while pending:
        element = pending.pop()
        if element is not root:
            found.append(element)  # pre-order, as FindAll returns them
        children = element.FindAllBuildCache(U.TreeScope_Children, condition, request)
        fresh = []
        for index in range(children.Length):
            child = children.GetElement(index)
            runtime_id = tuple(child.GetCachedPropertyValue(U.UIA_RuntimeIdPropertyId) or ())
            if int(child.GetCachedPropertyValue(U.UIA_NativeWindowHandlePropertyId) or 0) in skip_windows:
                continue
            if runtime_id not in seen:
                seen.add(runtime_id)
                fresh.append(child)
        pending.extend(reversed(fresh))
    return found
