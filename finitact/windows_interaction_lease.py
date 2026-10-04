"""Windows machine/session-wide named-mutex implementation of InteractionLease."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from typing import Iterator, Protocol

from .interaction_lease import (
    ExclusiveInputEnvironment,
    InputEnvironmentState,
    LeaseAbandoned,
    LeaseGrant,
    LeaseUnavailable,
)


class Indicator(Protocol):
    def delivering(self) -> AbstractContextManager: ...

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
MAX_WAIT_MS = 0xFFFFFFFE


@dataclass(frozen=True)
class WindowsInputScope:
    """Windows evidence behind one platform-neutral exclusive-environment guarantee."""

    environment: ExclusiveInputEnvironment
    session_id: int
    window_station: str
    desktop: str


def current_windows_input_scope(environment: ExclusiveInputEnvironment) -> WindowsInputScope:
    """Resolve the calling process's real session, window station and thread desktop."""

    if os.name != "nt":
        raise OSError("Windows input scope is only available on Windows")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.GetCurrentProcessId.restype = wintypes.DWORD
    kernel32.ProcessIdToSessionId.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = (wintypes.DWORD,)
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    process_id = kernel32.GetCurrentProcessId()
    session_id = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(process_id, ctypes.byref(session_id)):
        raise OSError("ProcessIdToSessionId failed")
    station = user32.GetProcessWindowStation()
    desktop = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    if not station or not desktop:
        raise OSError("could not resolve the current Windows input objects")
    return WindowsInputScope(
        environment=environment,
        session_id=int(session_id.value),
        window_station=_user_object_name(user32, station),
        desktop=_user_object_name(user32, desktop),
    )


def _user_object_name(user32, handle) -> str:
    import ctypes
    from ctypes import wintypes

    needed = wintypes.DWORD()
    user32.GetUserObjectInformationW.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.GetUserObjectInformationW(handle, 2, None, 0, ctypes.byref(needed))
    if needed.value <= 2:
        raise OSError("GetUserObjectInformationW could not size the object name")
    buffer = ctypes.create_unicode_buffer(needed.value // ctypes.sizeof(ctypes.c_wchar))
    if not user32.GetUserObjectInformationW(handle, 2, buffer, needed.value, ctypes.byref(needed)):
        raise OSError("GetUserObjectInformationW failed")
    return buffer.value


class MutexApi(Protocol):
    def CreateMutexW(self, attributes, initial_owner: bool, name: str): ...

    def WaitForSingleObject(self, handle, milliseconds: int) -> int: ...

    def ReleaseMutex(self, handle) -> bool: ...

    def CloseHandle(self, handle) -> bool: ...


class ProcessMutexState:
    """Process-wide state per mutex name (ADR-0013).

    The kept handle holds the named mutex object alive for this process's lifetime so that another
    owner's crash surfaces as ``WAIT_ABANDONED`` instead of silently destroying the mutex
    (BUG-0014). An abandonment is sticky per name: the ownership gained by it is deliberately never
    released, and later acquisitions in this process fail without waiting, because a wait on the
    owning thread would succeed recursively (BUG-0015).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handles: dict[str, object] = {}
        self._abandoned: dict[str, str] = {}

    def handle(self, api: MutexApi, name: str):
        with self._lock:
            handle = self._handles.get(name)
            if handle is None:
                handle = api.CreateMutexW(None, False, name)
                if handle:
                    self._handles[name] = handle
            return handle

    def abandoned_reason(self, name: str) -> str | None:
        with self._lock:
            return self._abandoned.get(name)

    def mark_abandoned(self, name: str, reason: str) -> None:
        with self._lock:
            self._abandoned.setdefault(name, reason)


_PROCESS_MUTEX_STATE = ProcessMutexState()


def _kernel32_mutex_api() -> MutexApi:
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    api.ReleaseMutex.restype = wintypes.BOOL
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    return api


def keep_current_desktop_mutex_open(
    *, api: MutexApi | None = None, process_state: ProcessMutexState = _PROCESS_MUTEX_STATE
) -> None:
    """Open this process's desktop mutex at startup so crashes before the first run are detected."""

    scope = current_windows_input_scope(ExclusiveInputEnvironment("process-mutex-keepalive", "startup"))
    if not process_state.handle(api or _kernel32_mutex_api(), _mutex_name(scope)):
        raise OSError("could not open the interaction mutex")


class WindowsNamedInteractionLease:
    """Serialize synthetic input across processes in one isolated input environment.

    `Local` is deliberate: Windows input environments are session-scoped, and the mutex name
    includes the asserted session/window-station/desktop identity.  Generation is validated by
    the environment manager but does not split the lock for the same native input destination.
    """

    def __init__(
        self,
        scope: WindowsInputScope,
        *,
        api: MutexApi | None = None,
        clock=time.monotonic,
        process_state: ProcessMutexState = _PROCESS_MUTEX_STATE,
    ) -> None:
        if api is None:
            if os.name != "nt":
                raise OSError("WindowsNamedInteractionLease is only available on Windows")
            api = _kernel32_mutex_api()
        self._api = api
        self._clock = clock
        self._scope = scope
        self._process_state = process_state

    @contextmanager
    def acquire(
        self, environment: ExclusiveInputEnvironment, *, deadline_monotonic: float
    ) -> Iterator[LeaseGrant]:
        if environment != self._scope.environment:
            raise LeaseUnavailable("exclusive environment does not match the Windows input scope")
        name = _mutex_name(self._scope)
        reason = self._process_state.abandoned_reason(name)
        if reason is not None:
            raise LeaseAbandoned(f"{reason} earlier in this process")
        handle = self._process_state.handle(self._api, name)
        if not handle:
            raise LeaseUnavailable("could not create the interaction mutex")

        remaining_ms = max(0, min(MAX_WAIT_MS, int((deadline_monotonic - self._clock()) * 1000)))
        wait_result = self._api.WaitForSingleObject(handle, remaining_ms)
        if wait_result == WAIT_TIMEOUT:
            raise LeaseUnavailable("interaction lease acquisition timed out")
        if wait_result == WAIT_ABANDONED:
            reason = "interaction lease owner exited unexpectedly"
            self._process_state.mark_abandoned(name, reason)
            raise LeaseAbandoned(reason)
        if wait_result != WAIT_OBJECT_0:
            raise LeaseUnavailable(f"interaction lease wait failed: {wait_result}")
        try:
            yield LeaseGrant(environment)
        finally:
            if not self._api.ReleaseMutex(handle):
                reason = "interaction mutex could not be released by its owner"
                self._process_state.mark_abandoned(name, reason)
                raise LeaseAbandoned(reason)


class WindowsSyntheticInputLease:
    """Hold ADR-0009's three layers for one bounded run (ADR-0012 B2).

    The first delivery acquires the mutex and checks idle time once; later deliveries in the same
    run reuse the held mutex without re-checking idle, because the run's own SendInput resets it.
    Each delivery still gets its own indicator ``delivering()`` span. ``close()`` ends the run.
    Windows mutex ownership is thread-affine, so ``close()`` must run on the acquiring thread; the
    coordinator executes a whole run on one worker thread.
    """

    def __init__(
        self,
        *,
        mutex: WindowsNamedInteractionLease,
        idle_time: "WindowsIdleTimePrecondition",
        indicator: Indicator,
        environment_state: InputEnvironmentState | None = None,
        clock=time.monotonic,
    ) -> None:
        self._mutex = mutex
        self._idle_time = idle_time
        self._indicator = indicator
        self._environment_state = environment_state
        self._clock = clock
        self._held: ExitStack | None = None
        self._grant: LeaseGrant | None = None
        self._origin_tick: int | None = None

    @contextmanager
    def acquire(
        self, environment: ExclusiveInputEnvironment, *, deadline_monotonic: float
    ) -> Iterator[LeaseGrant]:
        if self._held is None:
            stack = ExitStack()
            try:
                grant = stack.enter_context(self._mutex.acquire(environment, deadline_monotonic=deadline_monotonic))
                idle_seconds = self._idle_time.seconds_idle()
                if idle_seconds < self._idle_time.minimum_idle_seconds:
                    raise LeaseUnavailable(
                        "synthetic input idle-time precondition was not met: "
                        f"{idle_seconds:.1f}s < {self._idle_time.minimum_idle_seconds:.1f}s"
                    )
            except BaseException:
                stack.close()
                raise
            self._held, self._grant = stack, grant
            self._origin_tick = self._idle_time.idle_origin_tick()
        else:
            assert self._grant is not None
            if environment != self._grant.environment:
                raise LeaseUnavailable("run-scoped lease is held for a different input environment")
            # The mutex wait no longer bounds a reused lease, so the run deadline is enforced here.
            if self._clock() >= deadline_monotonic:
                raise LeaseUnavailable("run deadline reached before a later delivery")
        with self._indicator.delivering():
            yield self._grant

    def close(self) -> None:
        stack, grant, origin = self._held, self._grant, self._origin_tick
        self._held = self._grant = self._origin_tick = None
        if stack is None:
            return
        # Recorded at run end, not per delivery: SendInput updates the last-input tick
        # asynchronously, so a read right after the click can miss it (ADR-0021).
        if origin is not None:
            self._idle_time.note_own_input(origin)
        try:
            stack.close()
        except LeaseAbandoned as exc:
            if self._environment_state is not None and grant is not None:
                self._environment_state.block(grant.environment, f"run-scoped lease release failed: {exc}")
            raise


def _mutex_name(scope: WindowsInputScope) -> str:
    identity = "\0".join(
        (
            str(scope.session_id),
            scope.window_station,
            scope.desktop,
        )
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return f"Local\\FinitactSyntheticInput-{digest}"


class IdleTimeApi(Protocol):
    def get_last_input_tick(self) -> int: ...

    def get_tick_count(self) -> int: ...


class OwnInputLedger:
    """Process-wide record of the last-input tick our own finished run left behind (ADR-0021).

    If the session's last-input tick still equals it, nothing has touched the input queue since
    that run, so idle is measured from the idle origin that run itself verified.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._own_tick: int | None = None
        self._origin_tick: int | None = None

    def record(self, *, own_tick: int, origin_tick: int) -> None:
        with self._lock:
            self._own_tick, self._origin_tick = own_tick, origin_tick

    def origin_for(self, last_input_tick: int) -> int:
        with self._lock:
            if self._own_tick is not None and last_input_tick == self._own_tick:
                assert self._origin_tick is not None
                return self._origin_tick
        return last_input_tick


class WindowsIdleTimePrecondition:
    """Gate synthetic input on a minimum keyboard/mouse idle duration (ADR-0009, layer 2).

    This is a heuristic precondition, not proof of human absence: `GetLastInputInfo` is session-
    scoped and its own tick count "is not guaranteed to be incremental" -- Microsoft's docs name
    an event raised by `SendInput` itself as one cause, meaning this process's own synthetic input
    can feed the counter it is reading. Callers must not treat `satisfied()` as a security
    guarantee; it only narrows when synthetic input is attempted, alongside the named-mutex
    process lock (layer 1) and an on-screen "AI操作中" indicator (layer 3).
    """

    def __init__(
        self,
        *,
        minimum_idle_seconds: float,
        api: IdleTimeApi | None = None,
        ledger: OwnInputLedger | None = None,
    ) -> None:
        if minimum_idle_seconds <= 0:
            raise ValueError("minimum_idle_seconds must be positive")
        if api is None:
            if os.name != "nt":
                raise OSError("WindowsIdleTimePrecondition is only available on Windows")
            api = _RealIdleTimeApi()
        self.minimum_idle_seconds = minimum_idle_seconds
        self._api = api
        self._ledger = ledger
        self._last_origin: int | None = None

    def idle_origin_tick(self) -> int:
        """The tick idle was last measured from; valid after ``seconds_idle()``."""
        assert self._last_origin is not None
        return self._last_origin

    def seconds_idle(self) -> float:
        last = self._api.get_last_input_tick()
        origin = self._ledger.origin_for(last) if self._ledger is not None else last
        self._last_origin = origin
        # both dwTime and GetTickCount are 32-bit tick counts; unsigned subtraction modulo 2**32
        # is the documented way to handle the ~49.7-day wraparound.
        elapsed_ms = (self._api.get_tick_count() - origin) % (1 << 32)
        return elapsed_ms / 1000.0

    def note_own_input(self, origin_tick: int) -> None:
        if self._ledger is not None:
            self._ledger.record(own_tick=self._api.get_last_input_tick(), origin_tick=origin_tick)

    def satisfied(self) -> bool:
        return self.seconds_idle() >= self.minimum_idle_seconds


class _RealIdleTimeApi:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetLastInputInfo.argtypes = (ctypes.POINTER(LASTINPUTINFO),)
        user32.GetLastInputInfo.restype = wintypes.BOOL
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetTickCount.restype = wintypes.DWORD

        self._ctypes = ctypes
        self._LASTINPUTINFO = LASTINPUTINFO
        self._user32 = user32
        self._kernel32 = kernel32

    def get_last_input_tick(self) -> int:
        info = self._LASTINPUTINFO(cbSize=self._ctypes.sizeof(self._LASTINPUTINFO))
        if not self._user32.GetLastInputInfo(self._ctypes.byref(info)):
            raise OSError("GetLastInputInfo failed")
        return info.dwTime

    def get_tick_count(self) -> int:
        return self._kernel32.GetTickCount()
