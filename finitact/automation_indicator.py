"""Layers A and C of ADR-0009's synthetic-input defense: an on-screen "AI操作中" indicator.

A procedural nudge only -- it does not prevent input collision, it only informs a human who is
looking at the screen that synthetic input may arrive (ADR-0009's honesty requirement: this layer
must never be documented or treated as a guarantee).

Superseded by ADR-0010: the earlier Tk-in-a-subprocess implementation stole the foreground the
moment it mapped (BUG-0012), because Tk exposes no way to apply ``WS_EX_NOACTIVATE`` before a
window is first shown. This version builds the window with ``win32_overlay.Win32LayeredOverlay``,
which sets that style at creation time, so it can never be foreground-eligible even for one frame.
It is Windows-only; there is no cross-platform fallback (non-Windows automation targets are not a
goal here -- see ADR-0010's "OS可搬性").
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from .indicator_theme import (
    DEFAULT_THEME,
    Rect,
    Theme,
    corner_position,
    render_badge,
    render_reticle,
    reticle_origin,
    settle_seconds,
)
from .win32_overlay import Win32LayeredOverlay

_ANIMATION_FPS = 30


class AutomationIndicator:
    """Owns the run-long badge (layer A) and the per-delivery target reticle (layer C)."""

    def __init__(
        self,
        *,
        theme: Theme = DEFAULT_THEME,
        corner: str = "bottom_right",
        margin: int = 24,
        overlay_factory: Callable[..., Win32LayeredOverlay] | None = None,
        screen_size: Callable[[], tuple[int, int]] | None = None,
        window_origin: Callable[[int], tuple[int, int]] | None = None,
    ) -> None:
        if overlay_factory is None:
            if os.name != "nt":
                raise OSError("AutomationIndicator requires Windows-native Python")
            overlay_factory = Win32LayeredOverlay
        self._theme = theme
        self._corner = corner
        self._margin = margin
        self._overlay_factory = overlay_factory
        self._screen_size = screen_size or _primary_screen_size
        self._window_origin = window_origin or _window_screen_origin
        self._badge: Win32LayeredOverlay | None = None
        self._reticle_overlay: Win32LayeredOverlay | None = None

    def show(self) -> None:
        if self._badge is not None:
            raise RuntimeError("indicator is already showing")
        badge = self._overlay_factory(class_name="FinitactIndicatorBadge")
        badge.start()
        width, height = self._theme.badge_size
        screen_width, screen_height = self._screen_size()
        x, y = corner_position(self._corner, screen_width, screen_height, width, height, self._margin)
        badge.show(x=x, y=y, image=render_badge(self._theme, delivering=False))
        self._badge = badge

    def close(self) -> None:
        if self._badge is not None:
            self._badge.close()
            self._badge = None
        if self._reticle_overlay is not None:
            self._reticle_overlay.close()
            self._reticle_overlay = None

    @contextmanager
    def delivering(self) -> Iterator[None]:
        if self._badge is None:
            raise RuntimeError("indicator must be shown before a delivery")
        self._badge.update(image=render_badge(self._theme, delivering=True))
        try:
            yield
        finally:
            self._badge.update(image=render_badge(self._theme, delivering=False))

    @contextmanager
    def reticle(self, *, hwnd: int, rect: Rect, action: str = "click") -> Iterator[None]:
        overlay = self._reticle_overlay
        if overlay is None:
            overlay = self._overlay_factory(class_name="FinitactIndicatorReticle")
            overlay.start()
            self._reticle_overlay = overlay

        screen_left, screen_top = self._window_origin(hwnd)
        canvas_x, canvas_y = reticle_origin(self._theme, rect)
        x, y = screen_left + canvas_x, screen_top + canvas_y

        # Shown synchronously, before ``yield``: ADR-0010 requires the reticle to be visible
        # before delivery proceeds, not merely "eventually" once a background thread schedules it.
        overlay.show(x=x, y=y, image=render_reticle(self._theme, rect, action=action, progress=0.0))

        stop = threading.Event()

        def animate() -> None:
            start = time.monotonic()
            duration = settle_seconds(self._theme)
            loop = action == "wait"
            while not stop.wait(1.0 / _ANIMATION_FPS):
                elapsed = time.monotonic() - start
                progress = elapsed / duration if duration > 0 else 1.0
                overlay.update(image=render_reticle(self._theme, rect, action=action, progress=progress))
                if not loop and progress >= 1.0:
                    return

        thread = threading.Thread(target=animate, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1.0)
            overlay.hide()

    def __enter__(self) -> "AutomationIndicator":
        self.show()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _primary_screen_size() -> tuple[int, int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    SM_CXSCREEN, SM_CYSCREEN = 0, 1
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def _window_screen_origin(hwnd: int) -> tuple[int, int]:
    from ctypes import wintypes

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    rect = _RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    return rect.left, rect.top
