"""Windows seams for the screen-grounded adapter.

Capture is target-scoped ``PrintWindow`` rather than the desktop. Pointer delivery still uses
the shared Windows input queue, so this module does not construct or weaken the required
``SyntheticInputGuard``; callers must supply a runtime-verified exclusive environment.
"""

from __future__ import annotations

import base64
import gzip
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, Sequence

import numpy as np

from .action_adapter import MutationUncertain
from .automation_indicator import AutomationIndicator
from .interaction_lease import (
    DeliveryRefused,
    ExclusiveInputEnvironment,
    InputEnvironmentState,
    LeaseGrant,
    LeaseUnavailable,
    SyntheticInputGuard,
    SyntheticInputPolicy,
)
from .screen_grounded_adapter import (
    KEY_ALLOWLIST,
    SCREEN_OPERATIONS,
    ScreenGroundedAdapter,
    WindowFrame,
    deadline_after,
)
from .stage1_extractors import (
    CachingRegionExtractor,
    CompositeRegionExtractor,
    EdgeRectangleRegionExtractor,
    default_ocr_extractor,
)
from .windows_interaction_lease import (
    OwnInputLedger,
    WindowsIdleTimePrecondition,
    WindowsNamedInteractionLease,
    WindowsSyntheticInputLease,
    current_windows_input_scope,
)
from .windows_uia_adapter import PowerShellBridge, WindowsTarget

# Low enough that a prompt typed just before the outer agent's call passes; it only avoids colliding with a user
# who is still at the keyboard (ADR-0032). The environment variable restores a stricter gate.
DEFAULT_MINIMUM_IDLE_SECONDS = 1.0
MINIMUM_IDLE_ENV = "FINITACT_MIN_IDLE_SECONDS"


def minimum_idle_seconds() -> float:
    raw = os.environ.get(MINIMUM_IDLE_ENV, "").strip()
    if not raw:
        return DEFAULT_MINIMUM_IDLE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{MINIMUM_IDLE_ENV} must be a positive number of seconds, got {raw!r}") from None
    if not value > 0 or value == float("inf"):
        raise ValueError(f"{MINIMUM_IDLE_ENV} must be a positive number of seconds, got {raw!r}")
    return value

# Shared across every run_windows(screen:...) call in this process, keyed by ExclusiveInputEnvironment:
# a per-call InputEnvironmentState would forget a block the moment that run's adapter was discarded,
# defeating the point of blocking an environment after an uncertain delivery or lease-release
# failure (ADR-0009) -- the next run, with the same exclusive_environment_ref, must still see it.
_SHARED_INPUT_ENVIRONMENT_STATE = InputEnvironmentState()
# Process-wide so a later run can tell its own previous clicks from human input (ADR-0021).
_SHARED_OWN_INPUT_LEDGER = OwnInputLedger()


class WindowsInProcessCapture:
    """``WindowsPrintWindowCapture`` without a PowerShell process per frame (E2E-I11: 10.4 of 25.3 s in E2E-01).

    Same scope (root plus visible same-process windows it owns), same ``PrintWindow(PW_RENDERFULLCONTENT)``,
    same physical-pixel BGRA with opaque alpha, so frames, fingerprints and OCR input stay as before.
    """

    def __init__(self, target: WindowsTarget) -> None:
        self.target = target
        self._user32 = None

    def _load(self) -> None:
        # Bound on first capture so the factory still builds (and is tested) off Windows.
        if self._user32 is not None:
            return
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wintypes = ctypes, wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        u, g = self._user32, self._gdi32
        u.GetWindow.restype = wintypes.HWND
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
            wintypes.DWORD,
        ]  # fmt: skip
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteDC.argtypes = [wintypes.HDC]

    def capture(self) -> WindowFrame:
        return self._with_dpi(lambda: self._frame(self._checked_root()))

    def capture_scope(self) -> tuple[WindowFrame, ...]:
        def scope() -> tuple[WindowFrame, ...]:
            root = self._checked_root()
            frames = [self._frame(root)]
            for hwnd in self._owned(root):
                try:
                    frames.append(self._frame(hwnd))
                except RuntimeError:
                    continue
            return tuple(frames)

        return self._with_dpi(scope)

    def _with_dpi(self, work):
        self._load()
        # BUG-0017: physical pixels, the same Per-Monitor V2 context the PowerShell capture ran under.
        previous = self._user32.SetThreadDpiAwarenessContext(self._ctypes.c_void_p(-4))
        try:
            return work()
        finally:
            self._user32.SetThreadDpiAwarenessContext(self._ctypes.c_void_p(previous))

    def _pid(self, hwnd: int) -> int:
        pid = self._wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(self._wintypes.HWND(hwnd), self._ctypes.byref(pid))
        return pid.value

    def _checked_root(self) -> int:
        if self._pid(self.target.hwnd) != self.target.process_id:
            raise RuntimeError("configured HWND no longer belongs to the configured process")
        return self.target.hwnd

    def _owned(self, root: int) -> list[int]:
        ctypes, wintypes, user32 = self._ctypes, self._wintypes, self._user32
        found: list[int] = []

        def owned_by_root(hwnd) -> bool:
            owner = user32.GetWindow(hwnd, 4)
            for _depth in range(16):
                if not owner:
                    return False
                if int(owner) == root:
                    return True
                owner = user32.GetWindow(owner, 4)
            return False

        def visit(hwnd, _lparam):
            if hwnd and int(hwnd) != root and self._pid(int(hwnd)) == self.target.process_id:
                if user32.IsWindowVisible(hwnd) and owned_by_root(hwnd):
                    found.append(int(hwnd))
            return True

        user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
        return found

    def _frame(self, hwnd: int) -> WindowFrame:
        ctypes, wintypes, user32, gdi32 = self._ctypes, self._wintypes, self._user32, self._gdi32
        rect = wintypes.RECT()
        if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            raise RuntimeError("GetWindowRect failed")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            raise RuntimeError("window has no capturable area")
        header = (ctypes.c_uint32 * 10)(40, width, (-height) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        dc = gdi32.CreateCompatibleDC(None)
        bitmap = gdi32.CreateDIBSection(dc, header, 0, ctypes.byref(bits), None, 0)
        if not bitmap:
            gdi32.DeleteDC(dc)
            raise RuntimeError("CreateDIBSection failed")
        old = gdi32.SelectObject(dc, bitmap)
        try:
            if not user32.PrintWindow(wintypes.HWND(hwnd), dc, 2):
                raise RuntimeError("PrintWindow returned false")
            pixels = np.frombuffer(ctypes.string_at(bits, width * height * 4), dtype=np.uint8).reshape(-1, 4).copy()
        finally:
            gdi32.SelectObject(dc, old)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(dc)
        # GDI leaves the DIB's alpha at 0; the GDI+ bitmap the PowerShell path read reported it opaque.
        pixels[:, 3] = 255
        return WindowFrame(hwnd, self.target.process_id, width, height, pixels.tobytes())


class WindowsInProcessPointer:
    """The checks and sends of ``_pointer_script`` for click, gesture and wheel, without a PowerShell process.

    E2E-I20: the two PowerShell calls per scroll cycle were about 72% of it. Returns the same raw dict, so
    confirmation and ``_refusal`` are unchanged; keys, paste, caret and drag stay in PowerShell.
    """

    def __init__(self) -> None:
        self._user32 = None

    def _load(self) -> None:
        if self._user32 is not None:
            return
        import ctypes
        from ctypes import wintypes

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]

        self._ctypes, self._wintypes, self._INPUT = ctypes, wintypes, INPUT
        u = ctypes.WinDLL("user32", use_last_error=True)
        u.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        u.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.WindowFromPoint.restype = wintypes.HWND
        u.WindowFromPoint.argtypes = [wintypes.POINT]
        u.GetAncestor.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        u.SendInput.restype = wintypes.UINT
        u.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        self._kernel32 = ctypes.WinDLL("kernel32")
        self._user32 = u

    def deliver(
        self,
        hwnd: int,
        process_id: int,
        point: tuple[int, int],
        *,
        send: bool,
        foreground_hwnds: Sequence[int] | None = None,
        wheel: int = 0,
        gesture: str = "click",
        hit_hwnds: Sequence[int] | None = None,
    ) -> dict:
        self._load()
        # BUG-0017: the same physical-pixel space as capture and the PowerShell path.
        previous = self._user32.SetThreadDpiAwarenessContext(self._ctypes.c_void_p(-4))
        try:
            return self._deliver(
                hwnd, process_id, point, send, tuple(foreground_hwnds or (hwnd,)), wheel, gesture,
                tuple(hit_hwnds or (hwnd,)),
            )
        finally:
            self._user32.SetThreadDpiAwarenessContext(self._ctypes.c_void_p(previous))

    def _deliver(self, hwnd, process_id, point, send, allowed, wheel, gesture, hit_allowed) -> dict:
        ctypes, wintypes, u = self._ctypes, self._wintypes, self._user32
        target = wintypes.HWND(hwnd)
        rect = wintypes.RECT()
        valid = bool(u.GetWindowRect(target, ctypes.byref(rect)))
        screen = wintypes.POINT(rect.left + point[0], rect.top + point[1])
        if valid:
            self._front(target, allowed)
        hit = u.WindowFromPoint(screen) if valid else None
        hit_root = _handle(u.GetAncestor(hit, 2)) if hit else 0
        foreground = _handle(u.GetForegroundWindow())
        pid = wintypes.DWORD()
        if valid:
            u.GetWindowThreadProcessId(target, ctypes.byref(pid))
        pid_ok = pid.value == process_id
        valid = valid and pid_ok and foreground in allowed and hit_root in hit_allowed
        events = 0
        if valid and send:
            events = self._wheel(screen, wheel) if wheel else self._gesture(screen, gesture)
        return {
            "valid": valid, "sent": valid and send, "events": events, "key_events": 0,
            "hit_root": hit_root, "foreground": foreground, "pid_ok": pid_ok,
            "hit_root_name": self._describe(hit_root), "foreground_name": self._describe(foreground),
        }

    def _front(self, target, allowed: tuple[int, ...]) -> None:
        # ADR-0033: lifting the foreground lock timeout, restored before returning (same as FinitactPointer.Front).
        u, ctypes = self._user32, self._ctypes
        if _handle(u.GetForegroundWindow()) in allowed:
            return
        old = ctypes.c_uint(0)
        u.SystemParametersInfoW(0x2000, 0, ctypes.byref(old), 0)
        u.SystemParametersInfoW(0x2001, 0, None, 0x2)
        # BUG-0040: a resident server loses to a terminal holding the last real input, and the lock
        # lift alone never fronts the taskbar; sharing the foreground thread's input state does.
        fore_thread = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        own_thread = self._kernel32.GetCurrentThreadId()
        attached = bool(fore_thread) and fore_thread != own_thread and u.AttachThreadInput(own_thread, fore_thread, True)
        try:
            if u.IsIconic(target):
                u.ShowWindow(target, 9)
            if not attached and fore_thread and fore_thread != own_thread:
                # BUG-0052: UWP frames (ApplicationFrameHost) refuse the attach with access denied. Owning
                # the last input also permits SetForegroundWindow; a zero move is the input with no effect.
                self._send([(0, 0x0001, 0)])
            u.BringWindowToTop(target)
            u.SetForegroundWindow(target)
        finally:
            if attached:
                u.AttachThreadInput(own_thread, fore_thread, False)
            u.SystemParametersInfoW(0x2001, 0, ctypes.c_void_p(old.value), 0x2)
        for _ in range(20):
            if _handle(u.GetForegroundWindow()) in allowed:
                return
            time.sleep(0.05)

    def _send(self, events: Sequence[tuple[int, int, int]]) -> int:
        """``(type, flags_or_vk, data)``: mouse ``(0, flags, mouseData)`` or key ``(1, vk, flags)``."""

        inputs = (self._INPUT * len(events))()
        for entry, (kind, first, second) in zip(inputs, events):
            entry.type = kind
            if kind == 0:
                entry.u.mi.dwFlags, entry.u.mi.mouseData = first, second & 0xFFFFFFFF
            else:
                entry.u.ki.wVk, entry.u.ki.dwFlags = first, second
        return int(self._user32.SendInput(len(events), inputs, self._ctypes.sizeof(self._INPUT)))

    def _gesture(self, screen, gesture: str) -> int:
        if not self._user32.SetCursorPos(screen.x, screen.y):
            return 0
        down, up = _GESTURE_BUTTONS.get(gesture, (0x0002, 0x0004))
        modifier = {"ctrl_click": 0x11, "shift_click": 0x10}.get(gesture)
        if gesture == "hover":
            # A zero relative move still posts WM_MOUSEMOVE, which hover menus and tooltips wait for.
            events = [(0, 0x0001, 0)]
        else:
            events = [(0, down, 0), (0, up, 0)] * (2 if gesture == "double_click" else 1)
            if modifier:
                events = [(1, modifier, 0), *events, (1, modifier, _KEYEVENTF_KEYUP)]
        return self._send(events)

    def _wheel(self, screen, notches: int) -> int:
        # One WHEEL_DELTA event per notch: some toolkits drop the remainder of a multi-notch delta.
        if not self._user32.SetCursorPos(screen.x, screen.y):
            return 0
        return self._send([(0, 0x0800, 120 if notches > 0 else -120)] * abs(notches))

    def _describe(self, hwnd: int) -> str:
        ctypes, u = self._ctypes, self._user32
        title, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(self._wintypes.HWND(hwnd), title, 256)
        u.GetClassNameW(self._wintypes.HWND(hwnd), cls, 256)
        pid = self._wintypes.DWORD()
        u.GetWindowThreadProcessId(self._wintypes.HWND(hwnd), ctypes.byref(pid))
        # BUG-0047: a bare HWND made the outer agent guess the PID and retarget a mismatched window.
        return f"window:{hwnd}:{pid.value} {cls.value} '{title.value}'"


def _handle(value) -> int:
    return int(value or 0)


class WindowsPrintWindowCapture:
    def __init__(self, target: WindowsTarget, *, bridge: PowerShellBridge | None = None) -> None:
        self.target = target
        self.bridge = bridge or PowerShellBridge(timeout=30.0)

    def capture(self) -> WindowFrame:
        raw = self.bridge.run(_capture_script(self.target))
        if not raw.get("scope_ok"):
            raise RuntimeError("configured HWND no longer belongs to the configured process")
        if not raw.get("captured"):
            raise RuntimeError(str(raw.get("detail") or "PrintWindow failed"))
        pixels = gzip.decompress(base64.b64decode(str(raw["pixels_gzip_base64"]), validate=True))
        return WindowFrame(
            int(raw["hwnd"]),
            int(raw["process_id"]),
            int(raw["width"]),
            int(raw["height"]),
            pixels,
        )

    def capture_scope(self) -> tuple[WindowFrame, ...]:
        raw = self.bridge.run(_scope_capture_script(self.target))
        if not raw.get("scope_ok"):
            raise RuntimeError("configured HWND no longer belongs to the configured process")
        windows = raw.get("windows") or []
        if not windows or int(windows[0]["hwnd"]) != self.target.hwnd:
            raise RuntimeError(str(raw.get("detail") or "root window capture failed"))
        return tuple(
            WindowFrame(
                int(entry["hwnd"]),
                self.target.process_id,
                int(entry["width"]),
                int(entry["height"]),
                gzip.decompress(base64.b64decode(str(entry["pixels_gzip_base64"]), validate=True)),
            )
            for entry in windows
        )


class FillTargetRefused(DeliveryRefused):
    """The fill click was delivered but the caret shows no input took focus, so no keys were sent."""


class FillFocusChanged(DeliveryRefused):
    """The fill click completed, but focus left its scope before any key call."""


def _refusal(
    raw: Mapping, hwnd: int, foreground_hwnds: Sequence[int] | None, hit_hwnds: Sequence[int] | None = None
) -> str:
    # E2E-I30: the outer agent can only recover (e.g. move the covering window) if it is named.
    if raw.get("pid_ok") is False:
        return "target window belongs to another process"
    if int(raw.get("foreground") or 0) not in tuple(foreground_hwnds or (hwnd,)):
        return f"foreground stayed on {raw.get('foreground_name')}"
    if int(raw.get("hit_root") or 0) not in tuple(hit_hwnds or (hwnd,)):
        return f"point is covered by {raw.get('hit_root_name')}"
    return "target window is gone"


class WindowsSendInputPointer:
    """Foreground/hit-test checked SendInput pointer delivery.

    Both methods perform native checks. The second check closes the verification-to-send gap as
    far as one PowerShell/native call permits; it does not replace exclusive-environment proof.
    """

    def __init__(
        self,
        *,
        bridge: PowerShellBridge | None = None,
        text_delivery: str = "paste",
        native: WindowsInProcessPointer | None = None,
        peers: Callable[[int], Sequence[int]] | None = None,
        hidden_peers: Callable[[int], Sequence[int]] | None = None,
    ) -> None:
        if text_delivery not in ("paste", "keys"):
            raise ValueError("text_delivery must be paste or keys")
        self.bridge = bridge or PowerShellBridge()
        self.text_delivery = text_delivery
        self.native = native
        self.peers = peers
        self.hidden_peers = hidden_peers
        self.last_refusal: str | None = None
        self.last_input_focus = False

    def _scope(self, hwnd: int, foreground_hwnds: Sequence[int] | None, *, pointer: bool = True) -> dict:
        """Foreground and hit-test sets widened by ``hwnd``'s peer surfaces (BUG-0048); unchanged without peers.

        A hidden holder (BUG-0057) excuses only pointer-only gestures; anything sending keys excludes it.
        """

        peers = tuple(self.peers(hwnd)) if self.peers is not None else ()
        holders = tuple(self.hidden_peers(hwnd)) if pointer and self.hidden_peers is not None else ()
        if not peers and not holders:
            return {"foreground_hwnds": foreground_hwnds}
        foreground = tuple(foreground_hwnds or (hwnd,))
        return {
            "foreground_hwnds": foreground + tuple(p for p in (*peers, *holders) if p not in foreground),
            "hit_hwnds": (hwnd, *peers),
        }

    def _pointer(self, hwnd: int, process_id: int, point: tuple[int, int], **options) -> Mapping:
        if self.native is not None:
            if options.get("gesture", "click") not in _GESTURE_EVENTS:
                raise ValueError(f"unknown pointer gesture: {options['gesture']}")
            return self.native.deliver(hwnd, process_id, point, **options)
        return self.bridge.run(_pointer_script(hwnd, process_id, point, **options))

    def target_is_valid(
        self,
        *,
        hwnd: int,
        process_id: int,
        point: tuple[int, int],
        foreground_hwnds: Sequence[int] | None = None,
    ) -> bool:
        scope = self._scope(hwnd, foreground_hwnds)
        raw = self._pointer(hwnd, process_id, point, send=False, **scope)
        self.last_refusal = None if raw.get("valid") else _refusal(raw, hwnd, **scope)
        return bool(raw.get("valid")) and not bool(raw.get("sent"))

    def click(
        self,
        *,
        hwnd: int,
        process_id: int,
        point: tuple[int, int],
        foreground_hwnds: Sequence[int] | None = None,
        gesture: str = "click",
    ) -> bool:
        raw = self._pointer(hwnd, process_id, point, send=True, gesture=gesture, **self._scope(hwnd, foreground_hwnds))
        return bool(raw.get("valid")) and bool(raw.get("sent")) and int(raw.get("events", 0)) == _GESTURE_EVENTS[gesture]

    def type_text(
        self,
        *,
        hwnd: int,
        process_id: int,
        point: tuple[int, int],
        text: str,
        foreground_hwnds: Sequence[int] | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> bool:
        self.last_input_focus = False
        # An empty clipboard value makes Set-Clipboard throw after the click, which blocks the environment.
        paste = self.text_delivery == "paste" and bool(text)
        keys = key_events("Ctrl+A") + (
            _chord_events("Ctrl+V") if paste else text_events(text) if text else key_events("Delete")
        )
        script = _pointer_script(
            hwnd,
            process_id,
            point,
            send=True,
            keys=keys,
            paste=paste,
            caret_region=region,
            **self._scope(hwnd, foreground_hwnds, pointer=False),
        )
        raw = (
            self.bridge.run(script, stdin=base64.b64encode(text.replace("\n", "\r\n").encode("utf-8")).decode())
            if paste
            else self.bridge.run(script)
        )
        if (
            raw.get("focus_refused")
            and raw.get("valid")
            and raw.get("sent")
            and raw.get("events") == 2
            and raw.get("key_events") == 0
            and raw.get("keys_attempted") is False
        ):
            raise FillFocusChanged(
                "fill click delivered; no keys sent because focus moved to "
                f"{raw.get('focus_after_name')}. Observe that window and select its input before retrying fill."
            )
        if raw.get("caret_refused"):
            raise FillTargetRefused(
                f"caret stayed at {raw.get('caret_after')} outside fill region {list(region or ())}"
            )
        self.last_input_focus = bool(raw.get("caret_entered"))
        return (
            bool(raw.get("valid"))
            and bool(raw.get("sent"))
            and int(raw.get("events", 0)) == 2
            and int(raw.get("key_events", 0)) == len(keys)
        )

    def scroll(
        self,
        *,
        hwnd: int,
        process_id: int,
        point: tuple[int, int],
        notches: int,
        foreground_hwnds: Sequence[int] | None = None,
    ) -> bool:
        if notches == 0:
            raise ValueError("scroll needs a non-zero notch count")
        raw = self._pointer(hwnd, process_id, point, send=True, wheel=notches, **self._scope(hwnd, foreground_hwnds))
        return bool(raw.get("valid")) and bool(raw.get("sent")) and int(raw.get("events", 0)) == abs(notches)

    def drag(
        self,
        *,
        hwnd: int,
        process_id: int,
        start: tuple[int, int],
        end: tuple[int, int],
        foreground_hwnds: Sequence[int] | None = None,
        end_hwnd: int | None = None,
        end_process_id: int | None = None,
    ) -> bool:
        raw = self.bridge.run(
            _drag_script(
                hwnd, process_id, start, end, foreground_hwnds=foreground_hwnds,
                end_hwnd=end_hwnd, end_process_id=end_process_id,
            )
        )
        return (
            bool(raw.get("valid"))
            and bool(raw.get("sent"))
            and int(raw.get("events", 0)) == 2
            and int(raw.get("moves", 0)) == 8
        )

    def caret(self, *, hwnd: int) -> tuple[int, int, int, int] | None:
        """The focused input's MSAA caret relative to ``hwnd``'s window rect; read-only, None when unreadable."""

        raw = self.bridge.run(
            _POINTER_HEADER
            + f"""
$target = [IntPtr]{int(hwnd)}
$rect = New-Object FinitactPointer+RECT
$caret = if ([FinitactPointer]::GetWindowRect($target,[ref]$rect)) {{ [FinitactPointer]::Caret($target) }} else {{ $null }}
$relative = if ($caret) {{ @(($caret[0] - $rect.Left), ($caret[1] - $rect.Top), $caret[2], $caret[3]) }} else {{ $null }}
@{{ caret=$relative }} | ConvertTo-Json -Compress
"""
        )
        caret = raw.get("caret")
        return tuple(int(value) for value in caret) if caret else None

    def focus_is_valid(self, *, hwnd: int, process_id: int, foreground_hwnds: Sequence[int]) -> bool:
        return self.focus_target(hwnd=hwnd, process_id=process_id, foreground_hwnds=foreground_hwnds)[0]

    def focus_target(
        self, *, hwnd: int, process_id: int, foreground_hwnds: Sequence[int]
    ) -> tuple[bool, tuple[int, int, int, int] | None]:
        """Whether keys may be sent, and where they land relative to ``hwnd`` (None when unreadable)."""

        raw = self.bridge.run(_key_script(hwnd, process_id, self._key_foreground(hwnd, foreground_hwnds), ()))
        rect = raw.get("focus_rect")
        valid = bool(raw.get("valid")) and int(raw.get("key_events", 0)) == 0
        return valid, tuple(int(value) for value in rect) if rect else None

    def _key_foreground(self, hwnd: int, foreground_hwnds: Sequence[int]) -> Sequence[int]:
        return self._scope(hwnd, tuple(foreground_hwnds) or None, pointer=False)["foreground_hwnds"] or foreground_hwnds

    def press_key(self, *, hwnd: int, process_id: int, key: str, foreground_hwnds: Sequence[int]) -> bool:
        keys = key_events(key)
        raw = self.bridge.run(_key_script(hwnd, process_id, self._key_foreground(hwnd, foreground_hwnds), keys))
        return bool(raw.get("valid")) and int(raw.get("key_events", 0)) == len(keys)


class _DeniedLease:
    """Stands in for a lease that the guard's policy denial never actually touches.

    ``SyntheticInputGuard.execute`` checks ``policy.allowed`` before calling ``lease.acquire``,
    so with synthetic input disabled this is unreachable; it raises rather than silently no-op
    in case that ordering ever changes.
    """

    @contextmanager
    def acquire(
        self, environment: ExclusiveInputEnvironment, *, deadline_monotonic: float
    ) -> Iterator[LeaseGrant]:
        raise LeaseUnavailable("synthetic input is not allowed by run policy")
        yield  # pragma: no cover -- unreachable, keeps this a generator for the Protocol shape


def windows_screen_grounded_adapter_factory(request) -> ScreenGroundedAdapter:
    """Resolve the explicit ``screen:<HWND>:<PID>`` MCP target for the Stage1 visual path.

    Observation remains usable cross-platform. Synthetic delivery is enabled only in a native
    Windows process, where all three ADR-0009 layers can be constructed and checked live.
    """

    parts = request.target_id.split(":")
    if len(parts) != 3 or parts[0] != "screen" or not all(part.isdecimal() for part in parts[1:]):
        raise ValueError("target_id must use screen:<HWND>:<PID>")
    hwnd, process_id = (int(part) for part in parts[1:])
    if hwnd <= 0 or process_id <= 0:
        raise ValueError("target HWND and PID must be positive")
    target = WindowsTarget(hwnd, process_id)
    drop_captures = ()
    drop_id = getattr(request, "drop_target_id", None)
    if drop_id is not None:
        drop_parts = drop_id.split(":")
        if len(drop_parts) != 3 or drop_parts[0] != "window" or not all(p.isdecimal() for p in drop_parts[1:]):
            raise ValueError("drop_target_id must use window:<HWND>:<PID>")
        drop_captures = (WindowsInProcessCapture(WindowsTarget(int(drop_parts[1]), int(drop_parts[2]))),)

    from .windows_inventory import hidden_shell_surfaces, shell_surface_peers

    if request.synthetic_input_allowed:
        guard, indicator = _windows_synthetic_guard(request)
    else:
        indicator = None
        guard = SyntheticInputGuard(
            policy=SyntheticInputPolicy(allowed=False),
            lease=_DeniedLease(),
            environment_state=_SHARED_INPUT_ENVIRONMENT_STATE,
            validate_environment=lambda _environment: False,
        )
    return ScreenGroundedAdapter(
        capture=WindowsInProcessCapture(target),
        extractor=_shared_extractor(),
        pointer=WindowsSendInputPointer(
            native=WindowsInProcessPointer(), peers=shell_surface_peers, hidden_peers=hidden_shell_surfaces
        ),
        guard=guard,
        deadline_monotonic=deadline_after(request.deadline_ms / 1000),
        indicator=indicator,
        operations=tuple(op for op in SCREEN_OPERATIONS if op in request.allowed_operations),
        uia_reader=_shared_uia_reader(),
        uia_scroller=_SHARED_UIA_READER.scroll_into_view if _SHARED_UIA_READER is not None else None,
        uia_range_setter=_SHARED_UIA_READER.set_range if _SHARED_UIA_READER is not None else None,
        drop_captures=drop_captures,
    )


_SHARED_UIA_READER = None


def _shared_uia_reader():
    """ADR-0036; ``FINITACT_SCREEN_UIA=0`` keeps the OCR-only path (and a missing comtypes does too)."""

    global _SHARED_UIA_READER
    if os.name != "nt" or os.environ.get("FINITACT_SCREEN_UIA", "1") == "0":
        return None
    with _SHARED_EXTRACTOR_LOCK:
        if _SHARED_UIA_READER is None:
            try:
                from .windows_uia_reader import ComUiaReader

                _SHARED_UIA_READER = ComUiaReader()
            except ImportError:
                return None
    return _SHARED_UIA_READER.read


_SHARED_EXTRACTOR: CachingRegionExtractor | None = None
_SHARED_EXTRACTOR_LOCK = threading.Lock()


def _shared_extractor() -> CachingRegionExtractor:
    global _SHARED_EXTRACTOR
    with _SHARED_EXTRACTOR_LOCK:
        if _SHARED_EXTRACTOR is None:
            _SHARED_EXTRACTOR = CachingRegionExtractor(
                CompositeRegionExtractor((default_ocr_extractor(),), supplementary=(EdgeRectangleRegionExtractor(),))
            )
        return _SHARED_EXTRACTOR


def warm_up_shared_extractor() -> threading.Thread:
    """Build the OCR engine and run its first inference off the first run's clock.

    E2E-04's first run of every trial spent 2.7s in observe_extract against 0.15s on the same windows later;
    a Windows probe put 1.0s of a cold taskbar OCR on the engine alone. A thread, because the MCP client
    waits for the server's start-up; the shared extractor's lock makes a racing run wait for this one.
    """

    def warm() -> None:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (320, 64), "white")
        # Text, so recognition runs too: a blank frame stops after detection.
        ImageDraw.Draw(image).text((8, 20), "Finitact 0123", fill="black")
        r, g, b = image.split()
        pixels = Image.merge("RGBA", (b, g, r, Image.new("L", image.size, 255))).tobytes()
        try:
            _shared_extractor().extract(WindowFrame(1, 1, image.width, image.height, pixels))
        except Exception as exc:  # noqa: BLE001 - the first run builds the engine again and reports its failure
            print(f"finitact: OCR warm-up failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    thread = threading.Thread(target=warm, name="finitact-ocr-warm-up", daemon=True)
    thread.start()
    return thread


def _windows_synthetic_guard(request) -> tuple[SyntheticInputGuard, AutomationIndicator]:
    environment = ExclusiveInputEnvironment(str(request.exclusive_environment_ref), "runtime-scope-v1")
    try:
        initial_scope = current_windows_input_scope(environment)
        indicator = AutomationIndicator()
        lease = WindowsSyntheticInputLease(
            mutex=WindowsNamedInteractionLease(initial_scope),
            idle_time=WindowsIdleTimePrecondition(
                minimum_idle_seconds=minimum_idle_seconds(), ledger=_SHARED_OWN_INPUT_LEDGER
            ),
            indicator=indicator,
            environment_state=_SHARED_INPUT_ENVIRONMENT_STATE,
        )
    except OSError as exc:
        raise ValueError(
            "synthetic input requires the MCP server to run in Windows-native Python"
        ) from exc

    def same_live_scope(actual: ExclusiveInputEnvironment) -> bool:
        if actual != environment:
            return False
        current = current_windows_input_scope(actual)
        return (
            current.session_id,
            current.window_station,
            current.desktop,
        ) == (
            initial_scope.session_id,
            initial_scope.window_station,
            initial_scope.desktop,
        )

    guard = SyntheticInputGuard(
        policy=SyntheticInputPolicy(allowed=True, environment=environment),
        lease=lease,
        environment_state=_SHARED_INPUT_ENVIRONMENT_STATE,
        validate_environment=same_live_scope,
    )
    return guard, indicator


@dataclass(frozen=True)
class OwnedWindow:
    hwnd: int
    class_name: str
    title: str


class WindowsOwnedWindowEnumerator:
    """List a process's visible top-level windows, not just one target HWND.

    A target-window ``PrintWindow`` re-capture cannot see a popup that renders as its own
    top-level HWND (Unity's EditorGUI dropdowns, Win32 common dialogs). Outcome checks that only
    look at the acted-on window can therefore miss a real state change (phase-e-screen-grounded-
    seams.md, SendInput 1click live実施 2026-09-22).
    """

    def __init__(self, *, bridge: PowerShellBridge | None = None) -> None:
        self.bridge = bridge or PowerShellBridge()

    def list_visible(self, process_id: int) -> Sequence[OwnedWindow]:
        raw = self.bridge.run(_owned_windows_script(process_id))
        if not raw.get("valid"):
            raise RuntimeError(str(raw.get("detail") or "window enumeration failed"))
        return tuple(
            OwnedWindow(int(entry["hwnd"]), str(entry["class_name"]), str(entry["title"]))
            for entry in raw.get("windows", [])
        )


class WindowsWindowMessagePointer:
    """One target-scoped mouse-message probe; not a general synthetic click fallback."""

    def __init__(self, *, bridge: PowerShellBridge | None = None) -> None:
        self.bridge = bridge or PowerShellBridge()

    def hover(self, *, hwnd: int, process_id: int, point: tuple[int, int]) -> int:
        raw = self.bridge.run(_window_message_pointer_script(hwnd, process_id, point, phase="move"))
        if not raw.get("valid") or not raw.get("move_sent"):
            raise RuntimeError(str(raw.get("detail") or "target-scoped mouse move was not sent"))
        return int(raw["child_hwnd"])

    def click_once(
        self, *, hwnd: int, process_id: int, point: tuple[int, int], expected_child_hwnd: int
    ) -> bool:
        raw = self.bridge.run(
            _window_message_pointer_script(
                hwnd,
                process_id,
                point,
                phase="click",
                expected_child_hwnd=expected_child_hwnd,
            )
        )
        if raw.get("down_started") and not raw.get("sequence_complete"):
            raise MutationUncertain(str(raw.get("detail") or "mouse message sequence did not complete"))
        if not raw.get("valid") or not raw.get("sequence_complete"):
            raise RuntimeError(str(raw.get("detail") or "target-scoped click was not sent"))
        return True


def _capture_script(target: WindowsTarget) -> str:
    return _CAPTURE_HEADER + f"""
$hwnd = [IntPtr]{target.hwnd}
$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
$scopeOk = ($root -ne $null -and $root.Current.ProcessId -eq {target.process_id})
if (-not $scopeOk) {{
  @{{scope_ok=$false; captured=$false; detail='scope mismatch'}} | ConvertTo-Json -Compress
  exit
}}
$rect = New-Object FinitactScreenCapture+RECT
if (-not [FinitactScreenCapture]::GetWindowRect($hwnd, [ref]$rect)) {{
  @{{scope_ok=$true; captured=$false; detail='GetWindowRect failed'}} | ConvertTo-Json -Compress
  exit
}}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
$bitmap = [System.Drawing.Bitmap]::new(
  $width,$height,[System.Drawing.Imaging.PixelFormat]::Format32bppArgb
)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$dc = $graphics.GetHdc()
try {{ $captured = [FinitactScreenCapture]::PrintWindow($hwnd, $dc, 2) }} finally {{
  $graphics.ReleaseHdc($dc)
  $graphics.Dispose()
}}
if (-not $captured) {{
  $bitmap.Dispose()
  @{{scope_ok=$true; captured=$false; detail='PrintWindow returned false'}} | ConvertTo-Json -Compress
  exit
}}
$area = New-Object System.Drawing.Rectangle 0,0,$width,$height
$readOnly = [System.Drawing.Imaging.ImageLockMode]::ReadOnly
$pixelFormat = [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
$data = $bitmap.LockBits($area,$readOnly,$pixelFormat)
try {{
  if ($data.Stride -ne ($width * 4)) {{ throw 'unexpected bitmap stride' }}
  $bytes = New-Object byte[] ($width * $height * 4)
  [Runtime.InteropServices.Marshal]::Copy($data.Scan0,$bytes,0,$bytes.Length)
}} finally {{
  $bitmap.UnlockBits($data)
  $bitmap.Dispose()
}}
$memory = New-Object IO.MemoryStream
$gzip = New-Object IO.Compression.GZipStream($memory,[IO.Compression.CompressionMode]::Compress,$true)
$gzip.Write($bytes,0,$bytes.Length)
$gzip.Dispose()
$encoded = [Convert]::ToBase64String($memory.ToArray())
$memory.Dispose()
@{{
  scope_ok=$true; captured=$true; hwnd={target.hwnd}; process_id={target.process_id};
  width=$width; height=$height; pixels_gzip_base64=$encoded
}} | ConvertTo-Json -Compress
"""


def _scope_capture_script(target: WindowsTarget) -> str:
    return _SCOPE_CAPTURE_HEADER + f"""
$rootHwnd = [IntPtr]{target.hwnd}
$root = [System.Windows.Automation.AutomationElement]::FromHandle($rootHwnd)
if ($root -eq $null -or $root.Current.ProcessId -ne {target.process_id}) {{
  @{{scope_ok=$false; detail='scope mismatch'}} | ConvertTo-Json -Compress
  exit
}}
$handles = [FinitactScopeCapture]::OwnedScope($rootHwnd, {target.process_id})
$windows = New-Object System.Collections.Generic.List[object]
foreach ($handle in $handles) {{
  $rect = New-Object FinitactScopeCapture+RECT
  if (-not [FinitactScopeCapture]::GetWindowRect($handle, [ref]$rect)) {{ continue }}
  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  if ($width -le 0 -or $height -le 0) {{ continue }}
  $bitmap = [System.Drawing.Bitmap]::new($width,$height,[System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $dc = $graphics.GetHdc()
  try {{ $captured = [FinitactScopeCapture]::PrintWindow($handle, $dc, 2) }} finally {{
    $graphics.ReleaseHdc($dc)
    $graphics.Dispose()
  }}
  if (-not $captured) {{ $bitmap.Dispose(); continue }}
  $area = New-Object System.Drawing.Rectangle 0,0,$width,$height
  $data = $bitmap.LockBits(
    $area,[System.Drawing.Imaging.ImageLockMode]::ReadOnly,[System.Drawing.Imaging.PixelFormat]::Format32bppArgb
  )
  try {{
    if ($data.Stride -ne ($width * 4)) {{ throw 'unexpected bitmap stride' }}
    $bytes = New-Object byte[] ($width * $height * 4)
    [Runtime.InteropServices.Marshal]::Copy($data.Scan0,$bytes,0,$bytes.Length)
  }} finally {{
    $bitmap.UnlockBits($data)
    $bitmap.Dispose()
  }}
  $memory = New-Object IO.MemoryStream
  $gzip = New-Object IO.Compression.GZipStream($memory,[IO.Compression.CompressionMode]::Compress,$true)
  $gzip.Write($bytes,0,$bytes.Length)
  $gzip.Dispose()
  $windows.Add(@{{
    hwnd=$handle.ToInt64(); width=$width; height=$height;
    pixels_gzip_base64=[Convert]::ToBase64String($memory.ToArray())
  }})
  $memory.Dispose()
}}
@{{scope_ok=$true; windows=$windows.ToArray()}} | ConvertTo-Json -Compress -Depth 4
"""


def _pointer_script(
    hwnd: int,
    process_id: int,
    point: tuple[int, int],
    *,
    send: bool,
    foreground_hwnds: Sequence[int] | None = None,
    keys: Sequence[tuple[int, int, int]] = (),
    paste: bool = False,
    wheel: int = 0,
    caret_region: tuple[int, int, int, int] | None = None,
    gesture: str = "click",
    hit_hwnds: Sequence[int] | None = None,
) -> str:
    local_x, local_y = point
    if gesture not in _GESTURE_EVENTS:
        raise ValueError(f"unknown pointer gesture: {gesture}")
    deliver = (
        f"[FinitactPointer]::Wheel($screenPoint.X,$screenPoint.Y,{int(wheel)})"
        if wheel
        else "[FinitactPointer]::Click($screenPoint.X,$screenPoint.Y)"
        if gesture == "click"
        else f"[FinitactPointer]::Gesture($screenPoint.X,$screenPoint.Y,'{gesture}')"
    )
    should_send = "$true" if send else "$false"
    allowed_foreground = ",".join(str(int(value)) for value in (foreground_hwnds or (hwnd,)))
    allowed_hit = ",".join(str(int(value)) for value in (hit_hwnds or (hwnd,)))
    return _POINTER_HEADER + f"""
$target = [IntPtr]{hwnd}
$rect = New-Object FinitactPointer+RECT
$valid = [FinitactPointer]::GetWindowRect($target,[ref]$rect)
$allowedHit = [int64[]]@({allowed_hit})
$screenPoint = New-Object FinitactPointer+POINT
$screenPoint.X = $rect.Left + {local_x}
$screenPoint.Y = $rect.Top + {local_y}
$allowedForeground = [int64[]]@({allowed_foreground})
$fronted = if ($valid) {{ [FinitactPointer]::Front($target,$allowedForeground) }} else {{ $false }}
$hit = if ($valid) {{ [FinitactPointer]::WindowFromPoint($screenPoint) }} else {{ [IntPtr]::Zero }}
$hitRoot = [FinitactPointer]::GetAncestor($hit,2)
$foreground = [FinitactPointer]::GetForegroundWindow()
$actualPid = 0
if ($valid) {{ [void][FinitactPointer]::GetWindowThreadProcessId($target,[ref]$actualPid) }}
$foregroundOk = $allowedForeground -contains $foreground.ToInt64()
$valid = $valid -and $actualPid -eq {process_id} -and $foregroundOk -and ($allowedHit -contains $hitRoot.ToInt64())
$events = 0
{_remember_cursor(bool(keys))}
{_caret_before(caret_region)}
if ($valid -and {should_send}) {{ $events = {deliver} }}
{_keys_after_click(keys, paste, caret_region)}
{_restore_cursor(bool(keys))}
@{{
  valid=$valid; sent=($valid -and {should_send}); events=$events; key_events=$keyEvents;
  hit_root=$hitRoot.ToInt64(); foreground=$foreground.ToInt64(); pid_ok=($actualPid -eq {process_id});
  hit_root_name=[FinitactPointer]::Describe($hitRoot); foreground_name=[FinitactPointer]::Describe($foreground);
  caret_refused=$caretRefused; caret_entered=$caretEntered; caret_before=$caretBefore; caret_after=$caretAfter
  focus_refused=$focusRefused; keys_attempted=$keysAttempted;
  focus_after_name=$(if ($focusAfter -ne $null) {{ [FinitactPointer]::Describe($focusAfter) }} else {{ $null }})
}} | ConvertTo-Json -Compress
"""


def _drag_script(
    hwnd: int,
    process_id: int,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    foreground_hwnds: Sequence[int] | None = None,
    end_hwnd: int | None = None,
    end_process_id: int | None = None,
) -> str:
    """ADR-0045: with ``end_hwnd`` the end point is relative to that window and must hit-test to it; the
    foreground stays within the start window's set, since only the start window is brought forward."""

    if (end_hwnd is None) != (end_process_id is None):
        raise ValueError("a drop window needs both its HWND and PID")
    end_target = hwnd if end_hwnd is None else end_hwnd
    end_pid = process_id if end_process_id is None else end_process_id
    start_x, start_y = start
    end_x, end_y = end
    allowed_foreground = ",".join(str(int(value)) for value in (foreground_hwnds or (hwnd,)))
    return _POINTER_HEADER + f"""
$target = [IntPtr]{hwnd}
$rect = New-Object FinitactPointer+RECT
$valid = [FinitactPointer]::GetWindowRect($target,[ref]$rect)
$start = New-Object FinitactPointer+POINT
$start.X = $rect.Left + {start_x}; $start.Y = $rect.Top + {start_y}
$endTarget = [IntPtr]{end_target}
$endRect = New-Object FinitactPointer+RECT
$endValid = [FinitactPointer]::GetWindowRect($endTarget,[ref]$endRect)
$end = New-Object FinitactPointer+POINT
$end.X = $endRect.Left + {end_x}; $end.Y = $endRect.Top + {end_y}
$allowedForeground = [int64[]]@({allowed_foreground})
$fronted = if ($valid) {{ [FinitactPointer]::Front($target,$allowedForeground) }} else {{ $false }}
$startRoot = if ($valid) {{ [FinitactPointer]::GetAncestor([FinitactPointer]::WindowFromPoint($start),2) }} else {{ [IntPtr]::Zero }}
$endRoot = if ($valid) {{ [FinitactPointer]::GetAncestor([FinitactPointer]::WindowFromPoint($end),2) }} else {{ [IntPtr]::Zero }}
$foreground = [FinitactPointer]::GetForegroundWindow()
$actualPid = 0
$endPid = 0
if ($valid) {{ [void][FinitactPointer]::GetWindowThreadProcessId($target,[ref]$actualPid) }}
if ($valid) {{ [void][FinitactPointer]::GetWindowThreadProcessId($endTarget,[ref]$endPid) }}
$valid = $valid -and $endValid -and $actualPid -eq {process_id} -and $endPid -eq {end_pid} -and ($allowedForeground -contains $foreground.ToInt64()) -and $startRoot -eq $target -and $endRoot -eq $endTarget
$events = 0
if ($valid) {{ $events = [FinitactPointer]::Drag($start.X,$start.Y,$end.X,$end.Y,8) }}
@{{ valid=$valid; sent=($valid -and $events -ne 0); events=$events; moves=8;
    start_root=$startRoot.ToInt64(); end_root=$endRoot.ToInt64(); foreground=$foreground.ToInt64() }} | ConvertTo-Json -Compress
"""


def _remember_cursor(enabled: bool) -> str:
    return "$cursorBefore = New-Object FinitactPointer+POINT\n$cursorKnown = [FinitactPointer]::GetCursorPos([ref]$cursorBefore)" if enabled else ""


def _restore_cursor(enabled: bool) -> str:
    # A fill left the pointer on its target, whose hover UI then became the next fill's target
    # (BUG-0033). Click-only deliveries keep the pointer: menus and dropdowns depend on that hover.
    if not enabled:
        return ""
    return "if ($cursorKnown -and $events -ne 0) { [void][FinitactPointer]::SetCursorPos($cursorBefore.X,$cursorBefore.Y) }"


def _caret_before(region: tuple[int, int, int, int] | None) -> str:
    base = "$caretRefused = $false; $caretEntered = $false; $caretBefore = $null; $caretAfter = $null"
    return base + ("\nif ($valid) { $caretBefore = [FinitactPointer]::Caret($target) }" if region else "")


def _caret_gate(region: tuple[int, int, int, int] | None) -> str:
    """PowerShell that sets ``$caretRefused`` when the click left the caret where it was and outside ``region``.

    A click that focused no input (caption, tab title) leaves the previous caret in place, and Ctrl+A with
    the paste would then replace the previously focused field (BUG-0033). An input that opens elsewhere
    moves the caret, so only an unmoved caret outside the region is refused; an unreadable caret keeps
    the old behaviour. The caret width blinks between 1 and 0, so only its position is compared. An empty
    focused field keeps its caret at the text start, left of the OCR label when a glyph such as Discord's
    ``#`` is not read (BUG-0036), so the left edge allows two line heights on the same row.
    """

    if not region:
        return ""
    x, y, width, height = (int(value) for value in region)
    slack = 4
    lead = max(slack, 2 * height)
    return f"""$caretAfter = [FinitactPointer]::Caret($target)
  if ($caretBefore -ne $null -and $caretAfter -ne $null -and
      $caretBefore[0] -eq $caretAfter[0] -and $caretBefore[1] -eq $caretAfter[1]) {{
    $caretX = $caretAfter[0] - $rect.Left
    $caretY = $caretAfter[1] - $rect.Top + $caretAfter[3] / 2
    $caretRefused = -not ($caretX -ge {x - lead} -and $caretX -le {x + width + slack} -and
      $caretY -ge {y - slack} -and $caretY -le {y + height + slack})
  }}
  $caretEntered = ($null -ne $caretAfter) -and -not $caretRefused"""


def _keys_after_click(
    keys: Sequence[tuple[int, int, int]], paste: bool = False, caret_region: tuple[int, int, int, int] | None = None
) -> str:
    if not keys:
        return "$keyEvents = 0"
    set_clipboard = ""
    if paste:
        # Pasting skips editor auto-indent and per-key processing; the prior clipboard is not restored
        # because the target reads it asynchronously after Ctrl+V (README: fill replaces the clipboard).
        set_clipboard = (
            "Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("
            "[Console]::In.ReadToEnd().Trim())))"
        )
    # The click moves focus asynchronously; typing before the target handled it could land elsewhere,
    # so wait briefly and require the foreground to still be in scope before any key is sent.
    return f"""$keyEvents = 0; $keysAttempted = $false; $focusRefused = $false; $focusAfter = $null
if ($events -eq 2) {{
  Start-Sleep -Milliseconds 80
  {_caret_gate(caret_region)}
  $focusAfter = [FinitactPointer]::GetForegroundWindow()
  $focusRefused = -not ($allowedForeground -contains $focusAfter.ToInt64())
  if (-not $caretRefused -and -not $focusRefused) {{
    {set_clipboard}
    $keysAttempted = $true
    {_send_keys(keys)}
  }}
}}"""


def _send_keys(keys: Sequence[tuple[int, int, int]]) -> str:
    vks, scans, flags = (",".join(str(int(event[index])) for event in keys) for index in range(3))
    return f"$keyEvents = [FinitactPointer]::Keys([int[]]@({vks}),[int[]]@({scans}),[int[]]@({flags}))"


def _key_script(
    hwnd: int, process_id: int, foreground_hwnds: Sequence[int], keys: Sequence[tuple[int, int, int]]
) -> str:
    allowed_foreground = ",".join(str(int(value)) for value in foreground_hwnds) or str(hwnd)
    send = _send_keys(keys) if keys else ""
    return _POINTER_HEADER + f"""
$target = [IntPtr]{hwnd}
$allowedForeground = [int64[]]@({allowed_foreground})
$fronted = [FinitactPointer]::Front($target,$allowedForeground)
$foreground = [FinitactPointer]::GetForegroundWindow()
$actualPid = 0
[void][FinitactPointer]::GetWindowThreadProcessId($target,[ref]$actualPid)
$valid = $actualPid -eq {process_id} -and ($allowedForeground -contains $foreground.ToInt64())
$keyEvents = 0
if ($valid) {{ {send} }}
$focusRect = $null
if ($valid -and {"$false" if keys else "$true"}) {{
  $rootRect = New-Object FinitactPointer+RECT
  $focus = [FinitactPointer]::FocusRect($target)
  if ($focus -and [FinitactPointer]::GetWindowRect($target,[ref]$rootRect)) {{
    $focusRect = @(($focus[0] - $rootRect.Left), ($focus[1] - $rootRect.Top), $focus[2], $focus[3])
  }}
}}
@{{ valid=$valid; key_events=$keyEvents; foreground=$foreground.ToInt64(); focus_rect=$focusRect }} | ConvertTo-Json -Compress
"""


_KEYEVENTF_EXTENDEDKEY = 0x0001
# SendInput events each gesture sends; the count confirms delivery like click's 2.
_GESTURE_EVENTS = {
    "click": 2, "double_click": 4, "right_click": 2, "middle_click": 2, "hover": 1, "ctrl_click": 4, "shift_click": 4,
}
_KEYEVENTF_KEYUP = 0x0002
# MOUSEEVENTF down/up pairs; ctrl/shift/double clicks use the left button.
_GESTURE_BUTTONS = {"right_click": (0x0008, 0x0010), "middle_click": (0x0020, 0x0040)}
_KEYEVENTF_UNICODE = 0x0004
_VK = {
    "Enter": 0x0D, "Escape": 0x1B, "Tab": 0x09, "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22, "A": 0x41, "S": 0x53, "V": 0x56, "Z": 0x5A,
    "Space": 0x20, "Backspace": 0x08, "Delete": 0x2E, "Insert": 0x2D, "Alt": 0x12, "Apps": 0x5D,
    "C": 0x43, "X": 0x58, "Y": 0x59, "F": 0x46, "N": 0x4E, "O": 0x4F, "T": 0x54, "W": 0x57,
    **{f"F{n}": 0x6F + n for n in range(1, 13)},
}
_MODIFIER_VK = {"Ctrl": 0x11, "Shift": 0x10}
_EXTENDED = {"Up", "Down", "Left", "Right", "Home", "End", "PageUp", "PageDown", "Delete", "Insert", "Apps"}


def key_events(key: str) -> list[tuple[int, int, int]]:
    """``(vk, scan, flags)`` SendInput events for one allowlisted key or chord, modifiers released last."""

    if key not in KEY_ALLOWLIST:
        raise ValueError(f"key is not allowlisted: {key}")
    return _chord_events(key)


def _chord_events(key: str) -> list[tuple[int, int, int]]:
    *modifiers, main = key.split("+")
    extended = _KEYEVENTF_EXTENDEDKEY if main in _EXTENDED else 0
    down = [(_MODIFIER_VK[name], 0, 0) for name in modifiers] + [(_VK[main], 0, extended)]
    up = [(vk, 0, flags | _KEYEVENTF_KEYUP) for vk, _, flags in reversed(down)]
    return down + up


def text_events(text: str) -> list[tuple[int, int, int]]:
    """Unicode SendInput events; newline and tab go as their virtual keys, which editors expect."""

    events: list[tuple[int, int, int]] = []
    for char in text:
        if char in "\n\t":
            vk = _VK["Enter" if char == "\n" else "Tab"]
            events += [(vk, 0, 0), (vk, 0, _KEYEVENTF_KEYUP)]
            continue
        if ord(char) < 32:
            raise ValueError("text contains control characters")
        encoded = char.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[index : index + 2], "little")
            events += [(0, unit, _KEYEVENTF_UNICODE), (0, unit, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)]
    return events


def _owned_windows_script(process_id: int) -> str:
    return _OWNED_WINDOWS_HEADER + f"""
$unitSeparator = [char]0x1F
$raw = [FinitactWindowEnum]::ListForPid({process_id})
$windows = New-Object System.Collections.Generic.List[object]
foreach ($record in ($raw -split "`n")) {{
  if ([string]::IsNullOrEmpty($record)) {{ continue }}
  $parts = $record -split $unitSeparator, 3
  $windows.Add(@{{hwnd=[int64]$parts[0]; class_name=$parts[1]; title=$parts[2]}})
}}
# PS 5.1: `@($windows) | ConvertTo-Json` throws ArgumentException for a List[object] of
# hashtables; `.ToArray()` avoids the array-subexpression path that trips it.
@{{valid=$true; windows=$windows.ToArray()}} | ConvertTo-Json -Compress -Depth 4
"""


def _window_message_pointer_script(
    hwnd: int,
    process_id: int,
    point: tuple[int, int],
    *,
    phase: str,
    expected_child_hwnd: int | None = None,
) -> str:
    if phase not in {"move", "click"}:
        raise ValueError("phase must be move or click")
    local_x, local_y = point
    expected = int(expected_child_hwnd or 0)
    message_body = (
        "$moveSent = [FinitactMessagePointer]::Send($child,0x0200,[IntPtr]::Zero,$packed,500)"
        if phase == "move"
        else f"""
if ($child.ToInt64() -ne {expected}) {{
  @{{valid=$false; move_sent=$false; down_started=$false; sequence_complete=$false;
     child_hwnd=$child.ToInt64(); detail='child HWND changed after hover'}} | ConvertTo-Json -Compress
  exit
}}
$downStarted = $true
$downSent = [FinitactMessagePointer]::Send($child,0x0201,[IntPtr]1,$packed,500)
if (-not $downSent) {{
  @{{valid=$true; move_sent=$false; down_started=$true; sequence_complete=$false;
     child_hwnd=$child.ToInt64(); detail='WM_LBUTTONDOWN timed out or target exited'}} | ConvertTo-Json -Compress
  exit
}}
$sameTarget = [FinitactMessagePointer]::TargetStillMatches($root,$child,{process_id})
if (-not $sameTarget) {{
  @{{valid=$true; move_sent=$false; down_started=$true; sequence_complete=$false;
     child_hwnd=$child.ToInt64(); detail='target changed after WM_LBUTTONDOWN'}} | ConvertTo-Json -Compress
  exit
}}
$upSent = [FinitactMessagePointer]::Send($child,0x0202,[IntPtr]::Zero,$packed,500)
$sequenceComplete = $upSent
"""
    )
    result = (
        "@{valid=$true; move_sent=$moveSent; down_started=$false; sequence_complete=$false; "
        "child_hwnd=$child.ToInt64(); detail=$(if ($moveSent) {$null} else {'WM_MOUSEMOVE timed out'})} "
        "| ConvertTo-Json -Compress"
        if phase == "move"
        else "@{valid=$true; move_sent=$false; down_started=$downStarted; "
        "sequence_complete=$sequenceComplete; child_hwnd=$child.ToInt64(); "
        "detail=$(if ($sequenceComplete) {$null} else {'WM_LBUTTONUP timed out or target exited'})} "
        "| ConvertTo-Json -Compress"
    )
    return _MESSAGE_POINTER_HEADER + f"""
$root = [IntPtr]{hwnd}
$rect = New-Object FinitactMessagePointer+RECT
$valid = [FinitactMessagePointer]::GetWindowRect($root,[ref]$rect)
$screen = New-Object FinitactMessagePointer+POINT
$screen.X = $rect.Left + {local_x}
$screen.Y = $rect.Top + {local_y}
$child = if ($valid) {{ [FinitactMessagePointer]::WindowFromPoint($screen) }} else {{ [IntPtr]::Zero }}
$valid = $valid -and [FinitactMessagePointer]::Preflight($root,$child,{process_id})
if (-not $valid) {{
  @{{valid=$false; move_sent=$false; down_started=$false; sequence_complete=$false;
     child_hwnd=$child.ToInt64(); detail='native preflight failed'}} | ConvertTo-Json -Compress
  exit
}}
$client = $screen
if (-not [FinitactMessagePointer]::ScreenToClient($child,[ref]$client)) {{ throw 'ScreenToClient failed' }}
$packed = [FinitactMessagePointer]::PackPoint($client.X,$client.Y)
{message_body}
{result}
"""


# BUG-0017: without an explicit context, only scripts that happen to call
# AutomationElement.FromHandle become DPI-aware, so capture (physical pixels) and pointer delivery
# (virtualized logical coordinates) disagree by the monitor scale. Fail closed if unavailable.
_PER_MONITOR_DPI = r"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FinitactDpi {
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr context);
  [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
  [DllImport("user32.dll")] public static extern int GetAwarenessFromDpiAwarenessContext(IntPtr context);
}
'@
[void][FinitactDpi]::SetProcessDpiAwarenessContext([IntPtr]-4)
if ([FinitactDpi]::GetAwarenessFromDpiAwarenessContext([FinitactDpi]::GetThreadDpiAwarenessContext()) -ne 2) {
  throw 'per-monitor DPI awareness unavailable'
}
"""


_CAPTURE_HEADER = _PER_MONITOR_DPI + r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FinitactScreenCapture {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
}
'@
"""


_SCOPE_CAPTURE_HEADER = _PER_MONITOR_DPI + r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class FinitactScopeCapture {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr hwnd, uint cmd);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  public static bool OwnedBy(IntPtr hwnd, IntPtr root) {
    IntPtr owner = GetWindow(hwnd, 4);
    for (int depth = 0; owner != IntPtr.Zero && depth < 16; depth++) {
      if (owner == root) return true;
      owner = GetWindow(owner, 4);
    }
    return false;
  }
  public static IntPtr[] OwnedScope(IntPtr root, uint expectedPid) {
    var result = new List<IntPtr>();
    result.Add(root);
    EnumWindows((h, l) => {
      uint pid;
      GetWindowThreadProcessId(h, out pid);
      if (h != root && pid == expectedPid && IsWindowVisible(h) && OwnedBy(h, root)) result.Add(h);
      return true;
    }, IntPtr.Zero);
    return result.ToArray();
  }
}
'@
"""


_POINTER_HEADER = _PER_MONITOR_DPI + r"""
$ErrorActionPreference = 'Stop'
Add-Type -ReferencedAssemblies Accessibility -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FinitactPointer {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
  public static string Describe(IntPtr h) {
    var title = new System.Text.StringBuilder(256); GetWindowText(h, title, 256);
    var cls = new System.Text.StringBuilder(256); GetClassName(h, cls, 256);
    uint pid; GetWindowThreadProcessId(h, out pid);
    return "window:" + h.ToInt64() + ":" + pid + " " + cls + " '" + title + "'";
  }
  [StructLayout(LayoutKind.Sequential)] public struct GUITHREADINFO {
    public int cbSize, flags; public IntPtr hwndActive, hwndFocus, hwndCapture, hwndMenuOwner, hwndMoveSize, hwndCaret;
    public RECT rcCaret;
  }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION {
    [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki;
  }
  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT {
    public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo;
  }
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT {
    public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo;
  }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT point);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hwnd, int cmd);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint attach, uint to, bool on);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool SystemParametersInfo(uint action, uint param, ref uint value, uint flags);
  [DllImport("user32.dll", EntryPoint="SystemParametersInfo")] public static extern bool SetSystemParameter(uint action, uint param, IntPtr value, uint flags);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT point);
  [DllImport("user32.dll")] public static extern uint SendInput(uint count, INPUT[] inputs, int size);
  [DllImport("user32.dll")] public static extern bool GetGUIThreadInfo(uint thread, ref GUITHREADINFO info);
  [DllImport("oleacc.dll")] public static extern int AccessibleObjectFromWindow(
    IntPtr hwnd, uint id, ref Guid iid, [MarshalAs(UnmanagedType.IUnknown)] out object accessible);
  // MSAA OBJID_CARET of the root's GUI-thread focus, in screen pixels; null when unreadable.
  // VSCode has no system caret but exposes this one (BUG-0033 probe).
  public static int[] Caret(IntPtr root) {
    try {
      uint pid;
      var info = new GUITHREADINFO(); info.cbSize = Marshal.SizeOf(typeof(GUITHREADINFO));
      uint thread = GetWindowThreadProcessId(root, out pid);
      IntPtr focus = GetGUIThreadInfo(thread, ref info) && info.hwndFocus != IntPtr.Zero ? info.hwndFocus : root;
      var iid = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
      object accessible;
      if (AccessibleObjectFromWindow(focus, 0xFFFFFFF8, ref iid, out accessible) != 0 || accessible == null) return null;
      int left, top, width, height;
      ((Accessibility.IAccessible)accessible).accLocation(out left, out top, out width, out height, 0);
      if (width == 0 && height == 0 && left <= 0) return null;
      return new int[] { left, top, width, height };
    } catch (Exception) { return null; }
  }
  // E2E-I7: where a key lands, for the reticle: the caret, else the focused MSAA child (a desktop icon).
  public static int[] FocusRect(IntPtr root) {
    var caret = Caret(root);
    if (caret != null) return caret;
    try {
      uint pid;
      var info = new GUITHREADINFO(); info.cbSize = Marshal.SizeOf(typeof(GUITHREADINFO));
      uint thread = GetWindowThreadProcessId(root, out pid);
      IntPtr focus = GetGUIThreadInfo(thread, ref info) && info.hwndFocus != IntPtr.Zero ? info.hwndFocus : root;
      var iid = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
      object accessible;
      if (AccessibleObjectFromWindow(focus, 0xFFFFFFFC, ref iid, out accessible) != 0 || accessible == null) return null;
      var client = (Accessibility.IAccessible)accessible;
      object focused = client.accFocus;
      int left, top, width, height;
      if (focused is int) client.accLocation(out left, out top, out width, out height, focused);
      else if (focused is Accessibility.IAccessible) ((Accessibility.IAccessible)focused).accLocation(out left, out top, out width, out height, 0);
      else return null;
      if (width <= 0 || height <= 0) return null;
      return new int[] { left, top, width, height };
    } catch (Exception) { return null; }
  }
  // ADR-0033: the target is known, so the mechanism brings it forward itself. Lifting the
  // foreground lock timeout is the bypass that worked here where AttachThreadInput silently failed
  // (scripts/check_windows_mcp_screen_success_live.py); it is restored before returning.
  // BUG-0040: the taskbar, and any target while a terminal holds the last real input, also needs
  // the attach, so both are applied together.
  public static bool Front(IntPtr target, long[] allowed) {
    if (Array.IndexOf(allowed, GetForegroundWindow().ToInt64()) >= 0) return true;
    uint old = 0;
    SystemParametersInfo(0x2000, 0, ref old, 0);
    SetSystemParameter(0x2001, 0, IntPtr.Zero, 0x2);
    uint forePid;
    uint foreThread = GetWindowThreadProcessId(GetForegroundWindow(), out forePid);
    uint ownThread = GetCurrentThreadId();
    bool attached = foreThread != 0 && foreThread != ownThread && AttachThreadInput(ownThread, foreThread, true);
    try {
      if (IsIconic(target)) ShowWindow(target, 9);
      // BUG-0052: see WindowsInProcessPointer._front.
      if (!attached && foreThread != 0 && foreThread != ownThread) {
        var move = new INPUT[1];
        move[0].type = 0; move[0].u.mi.dwFlags = 0x0001;
        SendInput(1, move, Marshal.SizeOf(typeof(INPUT)));
      }
      BringWindowToTop(target);
      SetForegroundWindow(target);
    } finally {
      if (attached) AttachThreadInput(ownThread, foreThread, false);
      SetSystemParameter(0x2001, 0, new IntPtr(old), 0x2);
    }
    for (int i = 0; i < 20; i++) {
      if (Array.IndexOf(allowed, GetForegroundWindow().ToInt64()) >= 0) return true;
      System.Threading.Thread.Sleep(50);
    }
    return false;
  }
  public static uint Click(int x, int y) {
    if (!SetCursorPos(x, y)) return 0;
    var inputs = new INPUT[2];
    inputs[0].type = 0; inputs[0].u.mi.dwFlags = 0x0002;
    inputs[1].type = 0; inputs[1].u.mi.dwFlags = 0x0004;
    return SendInput(2, inputs, Marshal.SizeOf(typeof(INPUT)));
  }
  public static uint Gesture(int x, int y, string gesture) {
    if (!SetCursorPos(x, y)) return 0;
    var events = new System.Collections.Generic.List<INPUT>();
    Action<uint> mouse = flags => {
      var input = new INPUT(); input.type = 0; input.u.mi.dwFlags = flags; events.Add(input);
    };
    Action<ushort, uint> key = (vk, flags) => {
      var input = new INPUT(); input.type = 1; input.u.ki.wVk = vk; input.u.ki.dwFlags = flags; events.Add(input);
    };
    switch (gesture) {
      case "double_click": mouse(0x0002); mouse(0x0004); mouse(0x0002); mouse(0x0004); break;
      case "right_click": mouse(0x0008); mouse(0x0010); break;
      case "middle_click": mouse(0x0020); mouse(0x0040); break;
      // A zero relative move still posts WM_MOUSEMOVE, which hover menus and tooltips wait for.
      case "hover": mouse(0x0001); break;
      case "ctrl_click": key(0x11, 0); mouse(0x0002); mouse(0x0004); key(0x11, 0x0002); break;
      case "shift_click": key(0x10, 0); mouse(0x0002); mouse(0x0004); key(0x10, 0x0002); break;
      default: return 0;
    }
    var inputs = events.ToArray();
    return SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
  }
  // One WHEEL_DELTA event per notch: some toolkits drop the remainder of a multi-notch delta.
  public static uint Wheel(int x, int y, int notches) {
    if (notches == 0 || !SetCursorPos(x, y)) return 0;
    int count = Math.Abs(notches);
    var inputs = new INPUT[count];
    for (int i = 0; i < count; i++) {
      inputs[i].type = 0; inputs[i].u.mi.dwFlags = 0x0800;
      inputs[i].u.mi.mouseData = unchecked((uint)(notches > 0 ? 120 : -120));
    }
    return SendInput((uint)count, inputs, Marshal.SizeOf(typeof(INPUT)));
  }
  public static int Drag(int startX, int startY, int endX, int endY, int moves) {
    if (moves < 1 || !SetCursorPos(startX, startY)) return 0;
    var down = new INPUT[1]; down[0].type = 0; down[0].u.mi.dwFlags = 0x0002;
    if (SendInput(1, down, Marshal.SizeOf(typeof(INPUT))) != 1) return 0;
    bool complete = true;
    try {
      for (int i = 1; i <= moves; i++) {
        int x = startX + (endX - startX) * i / moves;
        int y = startY + (endY - startY) * i / moves;
        if (!SetCursorPos(x, y)) { complete = false; break; }
        System.Threading.Thread.Sleep(8);
      }
    } finally {
      var up = new INPUT[1]; up[0].type = 0; up[0].u.mi.dwFlags = 0x0004;
      if (SendInput(1, up, Marshal.SizeOf(typeof(INPUT))) != 1) complete = false;
    }
    return complete ? 2 : -1;
  }
  public static uint Keys(int[] vks, int[] scans, int[] flags) {
    var inputs = new INPUT[vks.Length];
    for (int i = 0; i < vks.Length; i++) {
      inputs[i].type = 1;
      inputs[i].u.ki.wVk = (ushort)vks[i]; inputs[i].u.ki.wScan = (ushort)scans[i]; inputs[i].u.ki.dwFlags = (uint)flags[i];
    }
    return SendInput((uint)inputs.Length, inputs, Marshal.SizeOf(typeof(INPUT)));
  }
}
'@
"""


_MESSAGE_POINTER_HEADER = _PER_MONITOR_DPI + r"""
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FinitactMessagePointer {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT point);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr hwnd, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hwnd);
  [DllImport("user32.dll")] public static extern IntPtr GetCapture();
  [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
  [DllImport("user32.dll")] public static extern bool ScreenToClient(IntPtr hwnd, ref POINT point);
  [DllImport("user32.dll", SetLastError=true)]
  public static extern IntPtr SendMessageTimeout(IntPtr hwnd, uint msg, IntPtr wp, IntPtr lp,
                                                 uint flags, uint timeout, out IntPtr result);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
  public static bool TargetStillMatches(IntPtr root, IntPtr child, int expectedPid) {
    uint pid; GetWindowThreadProcessId(child, out pid);
    return pid == expectedPid && GetAncestor(child, 2) == root && IsWindowVisible(child) && IsWindowEnabled(child);
  }
  public static bool Preflight(IntPtr root, IntPtr child, int expectedPid) {
    if (GetForegroundWindow() != root || GetCapture() != IntPtr.Zero) return false;
    int[] keys = { 1, 2, 4, 5, 6, 16, 17, 18 };
    foreach (int key in keys) if ((GetAsyncKeyState(key) & 0x8000) != 0) return false;
    return TargetStillMatches(root, child, expectedPid);
  }
  public static IntPtr PackPoint(int x, int y) {
    long packed = ((long)(ushort)y << 16) | (ushort)x;
    return new IntPtr(packed);
  }
  public static bool Send(IntPtr hwnd, uint msg, IntPtr wp, IntPtr lp, uint timeout) {
    IntPtr result;
    const uint flags = 0x0002 | 0x0020; // SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT
    return SendMessageTimeout(hwnd, msg, wp, lp, flags, timeout, out result) != IntPtr.Zero;
  }
}
'@
"""


_OWNED_WINDOWS_HEADER = _PER_MONITOR_DPI + r"""
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;
public static class FinitactWindowEnum {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  public static string ListForPid(uint targetPid) {
    var lines = new List<string>();
    EnumWindows((h, l) => {
      uint pid;
      GetWindowThreadProcessId(h, out pid);
      if (pid == targetPid && IsWindowVisible(h)) {
        var title = new StringBuilder(256);
        GetWindowText(h, title, 256);
        var cls = new StringBuilder(256);
        GetClassName(h, cls, 256);
        lines.Add(string.Format("{0}\u001f{1}\u001f{2}", h.ToInt64(), cls, title));
      }
      return true;
    }, IntPtr.Zero);
    return string.Join("\n", lines);
  }
}
'@
"""
