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
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Callable, Iterator

from .indicator_theme import (
    DEFAULT_THEME,
    INDICATOR_ENV_PREFIX,
    ORB_FPS,
    STILL_SECONDS,
    ProviderState,
    Rect,
    RenderedImage,
    Theme,
    corner_position,
    render_orb,
    render_reticle,
    reticle_origin,
    theme_from_env,
)
from .win32_overlay import Win32LayeredOverlay


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
        self._phase = ("idle", time.monotonic())
        # Heats ask the provider concurrently, so in-flight requests are counted, not flagged.
        self._provider_lock = threading.Lock()
        self._provider_calls = 0
        self._provider_changed = time.monotonic() - 1e6
        self._badge_stop = threading.Event()
        self._badge_thread: threading.Thread | None = None

    def show(self) -> None:
        if self._badge is not None:
            raise RuntimeError("indicator is already showing")
        badge = self._overlay_factory(class_name="FinitactIndicatorBadge")
        badge.start()
        width = height = self._theme.orb_size
        screen_width, screen_height = self._screen_size()
        x, y = corner_position(self._corner, screen_width, screen_height, width, height, self._margin)
        badge.show(x=x, y=y, image=self._badge_frame())
        self._badge = badge
        if self._theme.animated:
            self._badge_stop.clear()
            self._badge_thread = threading.Thread(target=self._animate_badge, args=(badge,), daemon=True)
            self._badge_thread.start()

    def phase(self, name: str) -> None:
        """Switch the orb's motion: observe, think, or the operation being delivered."""

        self._phase = (name, time.monotonic())
        self._repaint_still()

    def provider_activity(self, active: bool) -> None:
        """One provider request started (True) or ended (False); the spark is lit while any is in flight."""

        with self._provider_lock:
            was_active = self._provider_calls > 0
            self._provider_calls = max(0, self._provider_calls + (1 if active else -1))
            if (self._provider_calls > 0) != was_active:
                self._provider_changed = time.monotonic()
        self._repaint_still()

    def _badge_frame(self, now: float | None = None) -> RenderedImage:
        name, started = self._phase
        if not self._theme.animated:
            return render_orb(self._theme, name, STILL_SECONDS, ProviderState(self._provider_calls > 0, STILL_SECONDS))
        now = time.monotonic() if now is None else now
        provider = ProviderState(self._provider_calls > 0, now - self._provider_changed)
        return render_orb(self._theme, name, now - started, provider)

    def _repaint_still(self) -> None:
        badge = self._badge
        if badge is not None and not self._theme.animated:
            badge.update(image=self._badge_frame())

    def _animate_badge(self, badge: Win32LayeredOverlay) -> None:
        while not self._badge_stop.wait(1.0 / ORB_FPS):
            badge.update(image=self._badge_frame())

    def close(self) -> None:
        self._badge_stop.set()
        if self._badge_thread is not None:
            self._badge_thread.join(timeout=1.0)
            self._badge_thread = None
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
        yield

    @contextmanager
    def reticle(
        self, *, hwnd: int, rect: Rect, action: str = "click", heading: tuple[float, float] | None = None
    ) -> Iterator[None]:
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
        first = STILL_SECONDS if not self._theme.animated else 0.0
        overlay.show(x=x, y=y, image=render_reticle(self._theme, rect, action=action, t=first, heading=heading))
        if not self._theme.animated:
            try:
                yield
            finally:
                overlay.hide()
            return

        stop = threading.Event()

        def animate() -> None:
            start = time.monotonic()
            while not stop.wait(1.0 / ORB_FPS):
                elapsed = time.monotonic() - start
                overlay.update(image=render_reticle(self._theme, rect, action=action, t=elapsed, heading=heading))

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


CORNERS = ("bottom_right", "bottom_left", "top_right", "top_left")


def indicator_from_env(environ: Mapping[str, str]) -> AutomationIndicator:
    """The indicator with the ``FINITACT_INDICATOR_*`` look and placement; a malformed value raises."""

    corner = environ.get(INDICATOR_ENV_PREFIX + "CORNER", "").strip() or "bottom_right"
    if corner not in CORNERS:
        raise ValueError(f"{INDICATOR_ENV_PREFIX}CORNER must be one of {', '.join(CORNERS)}, got {corner!r}")
    margin = environ.get(INDICATOR_ENV_PREFIX + "MARGIN", "").strip() or "24"
    if not margin.isdigit():
        raise ValueError(f"{INDICATOR_ENV_PREFIX}MARGIN must be a non-negative integer of px, got {margin!r}")
    return AutomationIndicator(theme=theme_from_env(environ), corner=corner, margin=int(margin))


def _primary_screen_size() -> tuple[int, int]:
    """Right and bottom of the primary work area, so the badge never sits on the taskbar clock."""

    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    SPI_GETWORKAREA = 0x0030
    area = wintypes.RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0):
        return area.right, area.bottom
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
