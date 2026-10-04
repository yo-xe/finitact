"""Win32 case oracles for ``evaluation_runner`` (ADR-0014).

They read the target through ctypes in the evaluating process -- EnumWindows and WM_GETTEXT --
not through Finitact's UIA reader or the server's PowerShell enumeration, so a system under test that
misreads the screen cannot also mislead its own grader. The one UIA read (``automation_id_number``) is a
single property query of its own, for a display OCR cannot read (ADR-0014 追記1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .evaluation_runner import FAILURE, SUCCESS, UNDETERMINED, Judgement, Sample, TargetMismatch


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    process_id: int


class Win32Probe:
    """The few user32 reads the oracles need; replaced by a fake off Windows."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        ]
        self._user32.SendMessageTimeoutW.restype = wintypes.LPARAM

    def owner_pid(self, hwnd: int) -> int | None:
        if not self._user32.IsWindow(self._wintypes.HWND(hwnd)):
            return None
        pid = self._wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(self._wintypes.HWND(hwnd), self._ctypes.byref(pid))
        return pid.value

    def visible_windows(self, process_id: int) -> list[dict[str, Any]]:
        ctypes, wintypes, user32 = self._ctypes, self._wintypes, self._user32
        found: list[dict[str, Any]] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process_id and user32.IsWindowVisible(hwnd):
                class_name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_name, 256)
                found.append({"hwnd": int(hwnd), "class_name": class_name.value, "title": self.title(hwnd)})
            return True

        user32.EnumWindows(callback_type(callback), 0)
        return sorted(found, key=lambda window: window["hwnd"])

    def window_text(self, hwnd: int, *, timeout_ms: int = 500) -> str:
        """WM_GETTEXT, which the system marshals across processes (unlike GetWindowText for children)."""

        ctypes, wintypes, user32 = self._ctypes, self._wintypes, self._user32
        length = ctypes.c_size_t()
        # SMTO_ABORTIFHUNG: a hung target must read as unreadable, not stall the poller.
        if not user32.SendMessageTimeoutW(wintypes.HWND(hwnd), 0x000E, 0, 0, 0x0002, timeout_ms, ctypes.byref(length)):
            raise RuntimeError("WM_GETTEXTLENGTH timed out")
        buffer = ctypes.create_unicode_buffer(length.value + 1)
        copied = ctypes.c_size_t()
        if not user32.SendMessageTimeoutW(
            wintypes.HWND(hwnd), 0x000D, length.value + 1, ctypes.addressof(buffer), 0x0002, timeout_ms,
            ctypes.byref(copied),
        ):
            raise RuntimeError("WM_GETTEXT timed out")
        return buffer.value

    def title(self, hwnd) -> str:
        length = self._user32.GetWindowTextLengthW(hwnd)
        buffer = self._ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def uia_name(self, hwnd: int, automation_id: str) -> str:
        """The UIA Name of the sole descendant with ``automation_id``, queried apart from ComUiaReader."""

        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitialize()
        except OSError:
            pass  # this thread already joined COM
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        automation = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        root = automation.ElementFromHandle(self._wintypes.HWND(hwnd))
        found = root.FindAll(
            U.TreeScope_Descendants, automation.CreatePropertyCondition(U.UIA_AutomationIdPropertyId, automation_id)
        )
        if found.Length != 1:
            raise RuntimeError(f"expected one {automation_id!r} element, found {found.Length}")
        return found.GetElement(0).CurrentName

    def children(self, hwnd: int) -> list[dict[str, Any]]:
        ctypes, wintypes, user32 = self._ctypes, self._wintypes, self._user32
        found: list[dict[str, Any]] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(child, _lparam):
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(child, class_name, 256)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(child, ctypes.byref(pid))
            found.append({"hwnd": int(child), "class_name": class_name.value, "process_id": pid.value})
            return True

        user32.EnumChildWindows(wintypes.HWND(hwnd), callback_type(callback), 0)
        return found

    def capture(self, hwnd: int):
        """PrintWindow(PW_RENDERFULLCONTENT) into a DIB, in physical pixels (BUG-0017)."""

        from .screen_grounded_adapter import WindowFrame

        ctypes, wintypes, user32 = self._ctypes, self._wintypes, self._user32
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        ]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            rect = wintypes.RECT()
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                raise RuntimeError("GetWindowRect failed")
            width, height = rect.right - rect.left, rect.bottom - rect.top
            pid = self.owner_pid(hwnd)
            if width <= 0 or height <= 0 or not pid:
                raise RuntimeError("window has no capturable area")

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
                ]

            header = BITMAPINFOHEADER()
            header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            header.biWidth, header.biHeight = width, -height
            header.biPlanes, header.biBitCount = 1, 32
            bits = ctypes.c_void_p()
            dc = gdi32.CreateCompatibleDC(None)
            bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
            if not bitmap:
                gdi32.DeleteDC(dc)
                raise RuntimeError("CreateDIBSection failed")
            old = gdi32.SelectObject(dc, bitmap)
            try:
                if not user32.PrintWindow(wintypes.HWND(hwnd), dc, 2):
                    raise RuntimeError("PrintWindow returned false")
                pixels = ctypes.string_at(bits, width * height * 4)
            finally:
                gdi32.SelectObject(dc, old)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(dc)
        finally:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
        return WindowFrame(hwnd=hwnd, process_id=pid, width=width, height=height, pixels=pixels)



def _check_bound(probe, target: WindowTarget) -> None:
    if probe.owner_pid(target.hwnd) != target.process_id:
        raise TargetMismatch(f"HWND {target.hwnd} no longer belongs to PID {target.process_id}")


WindowMatch = Callable[[Mapping[str, Any]], bool]
# Readers take the probe and target so one oracle class serves every channel a case needs.
PopupReader = Callable[[Any, WindowTarget], list]
ValueReader = Callable[[Any, WindowTarget], str]


def class_is(class_name: str) -> WindowMatch:
    return lambda window: window["class_name"] == class_name


def title_is(title: str) -> WindowMatch:
    return lambda window: window["title"] == title


def same_pid_windows(match: WindowMatch) -> PopupReader:
    return lambda probe, target: [window for window in probe.visible_windows(target.process_id) if match(window)]


def menu_items_visible(items: Sequence[str], extractor, *, min_visible: int) -> PopupReader:
    """A menu drawn inside the target's own window (Blender) has no HWND and no bpy trace, so it
    counts as open when ``min_visible`` of its item labels are recognized in one fresh capture.
    Pick items that appear nowhere else in the window, so a closed menu cannot satisfy the count."""

    def read(probe, target: WindowTarget) -> list:
        labels = {region.label for region in extractor.extract(probe.capture(target.hwnd))}
        seen = sorted(set(items) & labels)
        return [{"menu_items": seen}] if len(seen) >= min_visible else []

    return read


def control_text(control_hwnd: int) -> ValueReader:
    def read(probe, target: WindowTarget) -> str:
        if probe.owner_pid(control_hwnd) != target.process_id:
            raise TargetMismatch(f"control HWND {control_hwnd} left PID {target.process_id}")
        return probe.window_text(control_hwnd)

    return read


def sole_child(probe, hwnd: int, class_name: str) -> int:
    """Resolve a case's control immediately before the run; ambiguity is a setup error, not a guess."""

    matches = [child["hwnd"] for child in probe.children(hwnd) if child["class_name"] == class_name]
    if len(matches) != 1:
        raise TargetMismatch(f"expected one {class_name} under HWND {hwnd}, found {len(matches)}")
    return matches[0]


def normalize_label(text: str) -> str:
    """OCR renders the same display with varying spaces, case and ``x``/``×``; compare on the rest."""

    return "".join(text.split()).casefold().replace("×", "x")


def automation_id_number(automation_id: str) -> ValueReader:
    """The calculator display after keyboard input carries a focus border that hides its digits from both
    OCR engines (BUG-0060), so the result is read from its UIA Name ("表示は 7 です" / "Display is 7")."""

    def read(probe, target: WindowTarget) -> str:
        name = probe.uia_name(target.hwnd, automation_id)
        numbers = re.findall(r"-?[\d,.]+", name)
        if len(numbers) != 1:
            raise RuntimeError(f"expected one number in {name!r}")
        return numbers[0].replace(",", "")

    return read


def ocr_value_right_of(anchor: str, extractor) -> ValueReader:
    """The nearest same-row line to the right of ``anchor`` (Unity Game View: the resolution after "Display 1")."""

    def read(probe, target: WindowTarget) -> str:
        regions = extractor.extract(probe.capture(target.hwnd))
        anchors = [region for region in regions if normalize_label(region.label) == normalize_label(anchor)]
        if len(anchors) != 1:
            raise RuntimeError(f"expected one {anchor!r} label, found {len(anchors)}")
        ax, ay, aw, ah = anchors[0].rect
        row = [
            region for region in regions
            if region.rect[0] >= ax + aw and region.rect[1] < ay + ah and ay < region.rect[1] + region.rect[3]
        ]
        if not row:
            raise RuntimeError(f"no line to the right of {anchor!r}")
        return normalize_label(min(row, key=lambda region: region.rect[0]).label)

    return read


def saved_text(path: str) -> ValueReader:
    """The file an editor target saved, read from disk; a trailing newline is the editor's choice, not the goal's."""

    def read(probe, target: WindowTarget) -> str:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8").replace("\r\n", "\n").rstrip("\n")

    return read


def target_state(path: str, field: Callable[[Mapping[str, Any]], Any], *, max_age_s: float = 2.0) -> ValueReader:
    """A state file the target writes about itself (``scripts/blender_state_writer.py``, the Phase I Tk list);
    it must be fresh and from the target PID."""

    import json
    import os
    import time

    def read(probe, target: WindowTarget) -> str:
        state = None
        last_error = None
        for _ in range(5):
            try:
                with open(path, encoding="utf-8") as handle:
                    state = json.load(handle)
                break
            except (json.JSONDecodeError, OSError) as exc:
                last_error = exc
                time.sleep(0.01)
        if state is None:
            raise RuntimeError("target state could not be read") from last_error
        age = time.time() - os.path.getmtime(path)
        if age > max_age_s:
            raise RuntimeError(f"target state is {age:.1f}s old; the writer is not running")
        if state["pid"] != target.process_id:
            raise TargetMismatch(f"target state is from PID {state['pid']}, not {target.process_id}")
        return str(field(state))

    return read


class PopupOpensOracle:
    """Single action: a popup appears relative to the baseline."""

    def __init__(self, target: WindowTarget, popups: PopupReader, *, probe=None) -> None:
        self.target = target
        self.popups = popups
        self.probe = probe or Win32Probe()

    def snapshot(self) -> Mapping[str, Any]:
        _check_bound(self.probe, self.target)
        return {"popups": self.popups(self.probe, self.target)}

    def initial_state_violation(self, baseline: Mapping[str, Any]) -> str | None:
        return f"{len(baseline['popups'])} matching popup(s) already open" if baseline["popups"] else None

    def judge(self, transitions: Sequence[Sample]) -> Judgement:
        if any(sample.state["popups"] for sample in transitions[1:]):
            return Judgement(SUCCESS, "matching popup observed after the baseline")
        return Judgement(FAILURE, "no matching popup at any polled state through the end of the run")


class PopupSelectOracle:
    """Two actions: a popup opens, closes, and the target-specific value reads ``expected``.

    ``read_value`` must read the value by recognition of that target's display or state, never by
    image difference. A popup that was never observed open leaves the case undetermined even when the
    value matches: that transition is part of the preregistered meaning and is not inferred (ADR-0014 5).
    """

    def __init__(
        self, target: WindowTarget, popups: PopupReader, read_value: ValueReader, expected: str, *, probe=None
    ) -> None:
        self.target = target
        self.popups = popups
        self.read_value = read_value
        self.expected = expected
        self.probe = probe or Win32Probe()

    def snapshot(self) -> Mapping[str, Any]:
        _check_bound(self.probe, self.target)
        return {"popups": self.popups(self.probe, self.target), "value": self.read_value(self.probe, self.target)}

    def initial_state_violation(self, baseline: Mapping[str, Any]) -> str | None:
        if baseline["popups"]:
            return f"{len(baseline['popups'])} matching popup(s) already open"
        if baseline["value"] == self.expected:
            return f"value already reads {self.expected!r}"
        return None

    def judge(self, transitions: Sequence[Sample]) -> Judgement:
        final = transitions[-1].state
        if final["popups"]:
            return Judgement(FAILURE, "popup still open when the run ended")
        opened = any(sample.state["popups"] for sample in transitions[1:])
        if final["value"] == self.expected:
            if opened:
                return Judgement(SUCCESS, f"popup opened and closed; value reads {self.expected!r}")
            return Judgement(UNDETERMINED, "value matches but the popup was never observed open")
        return Judgement(FAILURE, f"value reads {final['value']!r}, not {self.expected!r}")


class ValueOracle:
    """Single action judged on one target-specific value (UIA fill, calculator key, workspace tab)."""

    def __init__(
        self, target: WindowTarget, read_value: ValueReader, expected: str, *, initial: str | None = None, probe=None
    ) -> None:
        self.target = target
        self.read_value = read_value
        self.expected = expected
        self.initial = initial
        self.probe = probe or Win32Probe()

    def snapshot(self) -> Mapping[str, Any]:
        _check_bound(self.probe, self.target)
        return {"value": self.read_value(self.probe, self.target)}

    def initial_state_violation(self, baseline: Mapping[str, Any]) -> str | None:
        if baseline["value"] == self.expected:
            return f"value already reads {self.expected!r}"
        if self.initial is not None and baseline["value"] != self.initial:
            return f"value reads {baseline['value']!r}, not the registered {self.initial!r}"
        return None

    def judge(self, transitions: Sequence[Sample]) -> Judgement:
        value = transitions[-1].state["value"]
        if value == self.expected:
            return Judgement(SUCCESS, f"value reads {self.expected!r}")
        return Judgement(FAILURE, f"value reads {value!r}, not {self.expected!r}")
