"""Win32 layered-window host for the ADR-0010 visible indicator.

One instance owns one HWND and a dedicated thread with its own message loop. The window dies
with the thread (and the thread is a daemon, so it dies with the process): there is no subprocess
and no stdin surface, so BUG-0011's failure mode cannot recur. Every Win32 call that touches the
HWND happens on that thread; other threads only enqueue requests, because window messages and a
window's device-context lifecycle are thread-affine.

``WS_EX_NOACTIVATE`` is set at ``CreateWindowExW`` time, before the window is ever shown, unlike
the Tk implementation this replaces (BUG-0012): Tk could only apply the style after the window
had already mapped, by which point it could have already taken the foreground.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

from .indicator_theme import RenderedImage

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
SW_SHOWNA = 8
SW_HIDE = 0
SWP_NOACTIVATE = 0x0010
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0
PM_REMOVE = 0x0001
ERROR_CLASS_ALREADY_EXISTS = 1410
_COMMAND_POLL_SECONDS = 0.05

LRESULT = ctypes.c_ssize_t
# ``ctypes.WINFUNCTYPE`` only exists on Windows builds of ctypes; this module otherwise imports
# cleanly on any OS (its callers already guard every real use behind an ``os.name == "nt"``
# check), so the WNDPROC factory is deferred to first real use rather than built at import time.
# A registered class keeps the WNDPROC pointer it was registered with until the process exits, and
# every later window of that class calls it, so the trampoline must outlive any single overlay (BUG-0035).
_class_wndprocs: dict[str, object] = {}
_class_lock = threading.Lock()


def _wndproc_type():
    return ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),  # a WNDPROC instance, cast to void*; see _create_layered_window
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


class Win32LayeredOverlay:
    """One layered, click-through, never-activating window, painted from BGRA frames."""

    def __init__(self, *, class_name: str, startup_timeout: float = 5.0) -> None:
        self._class_name = class_name
        self._startup_timeout = startup_timeout
        self._commands: queue.Queue[tuple] = queue.Queue()
        self._ready: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("overlay is already started")
        thread = threading.Thread(target=self._run, daemon=True, name=f"finitact-overlay-{self._class_name}")
        self._thread = thread
        thread.start()
        try:
            kind, value = self._ready.get(timeout=self._startup_timeout)
        except queue.Empty:
            self._thread = None
            raise RuntimeError("overlay window did not start in time") from None
        if kind == "error":
            thread.join(timeout=1.0)
            self._thread = None
            raise RuntimeError("overlay window failed to start") from value

    def show(self, *, x: int, y: int, image: RenderedImage) -> None:
        self._send(("show", x, y, image))

    def update(self, *, image: RenderedImage) -> None:
        self._send(("update", image))

    def hide(self) -> None:
        self._send(("hide",))

    def close(self) -> None:
        if self._thread is None:
            return
        self._send(("stop",))
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            raise RuntimeError("overlay thread did not stop")
        self._thread = None

    def _send(self, command: tuple) -> None:
        if self._thread is None:
            raise RuntimeError("overlay has not been started")
        self._commands.put(command)

    def _run(self) -> None:
        try:
            user32, gdi32, hwnd = _create_layered_window(self._class_name)
        except Exception as error:  # pragma: no cover -- exercised only on real Windows
            self._ready.put(("error", error))
            return
        self._ready.put(("ready", None))
        current_pos = (0, 0)
        running = True
        try:
            while running:
                try:
                    command = self._commands.get(timeout=_COMMAND_POLL_SECONDS)
                except queue.Empty:
                    command = None
                if command is not None:
                    if command[0] == "stop":
                        running = False
                    elif command[0] == "hide":
                        user32.ShowWindow(hwnd, SW_HIDE)
                    elif command[0] == "show":
                        _, x, y, image = command
                        current_pos = (x, y)
                        _paint(user32, gdi32, hwnd, x, y, image)
                        user32.ShowWindow(hwnd, SW_SHOWNA)
                    elif command[0] == "update":
                        _, image = command
                        _paint(user32, gdi32, hwnd, current_pos[0], current_pos[1], image)
                _pump_messages(user32)
        finally:
            user32.DestroyWindow(hwnd)


def _pump_messages(user32) -> None:
    msg = _MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _create_layered_window(class_name: str):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _configure_prototypes(user32, gdi32, kernel32)

    hinstance = kernel32.GetModuleHandleW(None)
    with _class_lock:
        if class_name not in _class_wndprocs:
            _register_class(user32, hinstance, class_name)

    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        class_name,
        class_name,
        WS_POPUP,
        0,
        0,
        1,
        1,
        None,
        None,
        hinstance,
        None,
    )
    if not hwnd:
        raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")
    return user32, gdi32, hwnd


def _register_class(user32, hinstance, class_name: str) -> None:
    wndproc = _wndproc_type()(lambda hwnd, msg, wparam, lparam: user32.DefWindowProcW(hwnd, msg, wparam, lparam))
    wndclass = _WNDCLASSEXW()
    wndclass.cbSize = ctypes.sizeof(_WNDCLASSEXW)
    wndclass.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
    wndclass.hInstance = hinstance
    wndclass.lpszClassName = class_name
    if not user32.RegisterClassExW(ctypes.byref(wndclass)):
        error = ctypes.get_last_error()
        if error != ERROR_CLASS_ALREADY_EXISTS:
            raise OSError(f"RegisterClassExW failed: {error}")
    _class_wndprocs[class_name] = wndproc


def _paint(user32, gdi32, hwnd, x: int, y: int, image: RenderedImage) -> None:
    header = _BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    header.biWidth = image.width
    header.biHeight = -image.height  # top-down DIB, matching our top-to-bottom pixel buffer
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = BI_RGB
    bitmap_info = _BITMAPINFO()
    bitmap_info.bmiHeader = header
    bits_ptr = ctypes.c_void_p()
    hbitmap = gdi32.CreateDIBSection(None, ctypes.byref(bitmap_info), DIB_RGB_COLORS, ctypes.byref(bits_ptr), None, 0)
    if not hbitmap:
        raise OSError(f"CreateDIBSection failed: {ctypes.get_last_error()}")
    try:
        ctypes.memmove(bits_ptr, image.bgra_premultiplied, len(image.bgra_premultiplied))
        mem_dc = gdi32.CreateCompatibleDC(None)
        try:
            previous = gdi32.SelectObject(mem_dc, hbitmap)
            try:
                screen_dc = user32.GetDC(None)
                try:
                    dst, src = _POINT(x, y), _POINT(0, 0)
                    size = _SIZE(image.width, image.height)
                    blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
                    ok = user32.UpdateLayeredWindow(
                        hwnd,
                        screen_dc,
                        ctypes.byref(dst),
                        ctypes.byref(size),
                        mem_dc,
                        ctypes.byref(src),
                        0,
                        ctypes.byref(blend),
                        ULW_ALPHA,
                    )
                    if not ok:
                        raise OSError(f"UpdateLayeredWindow failed: {ctypes.get_last_error()}")
                finally:
                    user32.ReleaseDC(None, screen_dc)
            finally:
                gdi32.SelectObject(mem_dc, previous)
        finally:
            gdi32.DeleteDC(mem_dc)
    finally:
        gdi32.DeleteObject(hbitmap)


def _configure_prototypes(user32, gdi32, kernel32) -> None:
    user32.RegisterClassExW.argtypes = (ctypes.POINTER(_WNDCLASSEXW),)
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = (
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    )
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = (wintypes.HWND,)
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = (
        ctypes.POINTER(_MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    )
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = (ctypes.POINTER(_MSG),)
    user32.DispatchMessageW.argtypes = (ctypes.POINTER(_MSG),)
    user32.DispatchMessageW.restype = LRESULT
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.UpdateLayeredWindow.argtypes = (
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(_POINT),
        ctypes.POINTER(_SIZE),
        wintypes.HDC,
        ctypes.POINTER(_POINT),
        wintypes.COLORREF,
        ctypes.POINTER(_BLENDFUNCTION),
        wintypes.DWORD,
    )
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(_BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    )
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
