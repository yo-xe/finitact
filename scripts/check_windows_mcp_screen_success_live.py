"""Bounded Windows-native MCP probe for a live screen-grounded single-click success case.

Companion to ``check_windows_mcp_mutex_live.py``, which proves the fail-closed mutex path. This
script proves the opposite leg of Phase F: a real ``run_windows(screen:...)`` call, through the
public STDIO transport and ``WindowsRunCoordinator``, that acquires all three ADR-0009 layers
(named mutex, real >=30s idle-time precondition, visible indicator) and delivers one real
``SendInput`` click to a disposable Tk target. Outcome is judged by ``WindowsOwnedWindowVerifier``,
an independent Win32 ``EnumWindows`` channel distinct from the SendInput delivery path, then
cross-checked a second time from this launcher process via a separate ctypes ``EnumWindows`` call.

This is NOT the preregistered ``screen-unity-open-dropdown-001`` case (docs/evaluations/
windows-mcp-replacement/case-manifest.json), which requires a live Unity target for the Phase I
windows-mcp comparison. That case stays ``not_run``. This script only establishes that the
transport/gate/delivery/outcome mechanism itself works end to end on a disposable target.

Candidate discovery uses the production default extractor unmodified (ADR-0016 OCR + edge). The
provider picks the candidate whose label reads CLICK, so a pass also shows the default OCR found
the target text inside the full server path.

The idle-time gate is a genuine precondition, not something this probe may bypass: it polls real
``GetLastInputInfo`` and refuses to proceed until >=32s have elapsed with no physical keyboard/mouse
input, aborting with a clear message rather than waiting silently forever.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

IDLE_MINIMUM_SECONDS = 32.0
TARGET_LABEL = "CLICK"
IDLE_POLL_TIMEOUT_SECONDS = 180.0
POPUP_TITLE = "FINITACT-POPUP-OPENED"


def _seconds_idle() -> float:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("GetLastInputInfo failed")
    elapsed_ms = (kernel32.GetTickCount() - info.dwTime) % (1 << 32)
    return elapsed_ms / 1000.0


def _wait_for_idle(*, minimum_seconds: float, timeout_seconds: float) -> float:
    deadline = time.monotonic() + timeout_seconds
    while True:
        idle = _seconds_idle()
        print(f"idle={idle:.1f}s", file=sys.stderr, flush=True)
        if idle >= minimum_seconds:
            return idle
        if time.monotonic() >= deadline:
            raise TimeoutError(f"idle time did not reach {minimum_seconds}s within {timeout_seconds}s")
        time.sleep(1.0)


def _force_foreground(hwnd: int, *, timeout_seconds: float = 5.0) -> None:
    """Foreground the disposable target window from this launcher process.

    A background-launched subprocess's window has no foreground-change rights of its own
    (Windows' focus-stealing prevention), so ``target_is_valid``'s ``foreground == target``
    check fails 3/3 without this (BUG-0011 follow-up). This is probe-only scaffolding to put
    the target into the state a real interactive window already has; production never does this
    (``docs/evaluations/windows-mcp-replacement/phase-f-native-mcp.md``).
    """

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    target = wintypes.HWND(hwnd)
    SW_SHOW = 5
    SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
    SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
    SPIF_SENDCHANGE = 0x2

    # AttachThreadInput-based foregrounding is unreliable on this machine (observed silent
    # SetForegroundWindow failures with GetLastError=0). Disabling the foreground lock timeout
    # for the duration of this call is the documented, session-scoped bypass -- it touches
    # window-activation policy only, not physical input, so it does not reset the idle-time
    # precondition this probe depends on. Restored unconditionally below.
    old_timeout = wintypes.UINT(0)
    user32.SystemParametersInfoW(SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(old_timeout), 0)
    user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, None, SPIF_SENDCHANGE)
    # BUG-0059: launched through WSL interop the lock lift alone loses to the terminal holding the
    # last real input; same fallbacks as the production _front (BUG-0040 attach, BUG-0052 zero move).
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    fore_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    own_thread = kernel32.GetCurrentThreadId()
    attached = bool(fore_thread) and fore_thread != own_thread and bool(
        user32.AttachThreadInput(own_thread, fore_thread, True)
    )
    moved = False
    try:
        if not attached and fore_thread and fore_thread != own_thread:
            user32.mouse_event(0x0001, 0, 0, 0, 0)
            moved = True
        show_ok = bool(user32.ShowWindow(target, SW_SHOW))
        top_ok = bool(user32.BringWindowToTop(target))
        set_ok = bool(user32.SetForegroundWindow(target))
    finally:
        if attached:
            user32.AttachThreadInput(own_thread, fore_thread, False)
        user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(old_timeout.value), SPIF_SENDCHANGE
        )
    print(
        f"force-foreground hwnd={hwnd} show_ok={show_ok} top_ok={top_ok} set_ok={set_ok}"
        f" attached={attached} moved={moved}",
        file=sys.stderr,
        flush=True,
    )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.1)
    raise RuntimeError(f"could not bring target window {hwnd} to foreground within {timeout_seconds}s")


def _list_top_level_titles(pid: int) -> list[tuple[int, str]]:
    """A third, independent EnumWindows path: ctypes in this launcher process, not PowerShell."""

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    results: list[tuple[int, str]] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            results.append((hwnd, buffer.value))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return results


def _read_json_line(process: subprocess.Popen[str], *, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline() if process.stdout is not None else ""
        if line:
            return json.loads(line)
        if process.poll() is not None:
            raise RuntimeError(f"child exited before readiness: {process.returncode}")
        time.sleep(0.02)
    raise TimeoutError("child readiness timed out")


def _target() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("Finitact disposable screen-success probe")
    # Borderless: a decorated Tk toplevel's title bar is a *second*, separately-repainted HWND
    # on Windows (winfo_id() names the inner client HWND; GA_ROOT resolves to the outer frame),
    # and its active/inactive repaint between the two freshness captures changed the edge count
    # extractor saw (8 vs 10 regions, observed live), tripping the "target is stale" guard. A
    # borderless window has one HWND and no such repaint.
    root.overrideredirect(True)
    root.geometry("260x160+80+80")
    canvas = tk.Canvas(root, width=240, height=140, bg="white", highlightthickness=0)
    canvas.pack(padx=10, pady=10)
    canvas.create_rectangle(40, 45, 200, 105, outline="black", width=6)
    canvas.create_text(120, 75, text="CLICK", fill="black", font=("Segoe UI", 18, "bold"))

    def on_click(_event) -> None:
        popup = tk.Toplevel(root)
        popup.title(POPUP_TITLE)
        popup.geometry("180x80+400+80")
        tk.Label(popup, text="opened").pack(padx=10, pady=10)
        popup.update()

    canvas.bind("<Button-1>", on_click)
    root.update()
    # winfo_id() is Tk's own drawable HWND, which on Windows is a *child* of the real top-level
    # frame window; WindowsSendInputPointer's hit-test resolves GA_ROOT and expects that, not
    # this child, to be the configured target (observed live: hit_root pointed at a different,
    # correctly-titled HWND than the one winfo_id() reported).
    GA_ROOT = 2
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.restype = wintypes.HWND
    top_level_hwnd = user32.GetAncestor(wintypes.HWND(root.winfo_id()), GA_ROOT)
    print(json.dumps({"hwnd": top_level_hwnd, "pid": os.getpid()}), flush=True)
    root.mainloop()


def _server() -> None:
    from finitact import mcp_server
    from finitact.decision_cache import InMemoryDecisionCache
    from finitact.contracts import Decision
    from finitact.runs import WindowsRunCoordinator
    from finitact.windows_adapter_router import windows_adapter_factory
    from finitact.windows_outcome_verifier import WindowsOwnedWindowVerifier, title_is, window_appeared
    from finitact.windows_screen_grounded import WindowsOwnedWindowEnumerator, WindowsSendInputPointer

    target_pid = int(os.environ["FINITACT_TARGET_PID"])
    trace = Path(os.environ["FINITACT_TRACE_PATH"])
    events: list[str] = []

    def log(event: str) -> None:
        events.append(f"{time.monotonic():.6f} {event}")
        trace.write_text("\n".join(events) + "\n", encoding="utf-8")

    click_calls: list[dict] = []
    original_click = WindowsSendInputPointer.click

    def traced_click(self, *, hwnd, process_id, point):
        log(f"click-start point={point}")
        try:
            result = original_click(self, hwnd=hwnd, process_id=process_id, point=point)
        except Exception as exc:
            log(f"click-error {type(exc).__name__}: {exc}")
            raise
        click_calls.append({"hwnd": hwnd, "process_id": process_id, "point": point, "confirmed": result})
        log(f"click-end confirmed={result}")
        return result

    from finitact.windows_screen_grounded import _pointer_script

    def _describe_hwnd(value: int) -> str:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        h = wintypes.HWND(value)
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(h, buf, 256)
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(h, cls, 256)
        owner_pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(h, ctypes.byref(owner_pid))
        return f"hwnd={value} title={buf.value!r} class={cls.value!r} pid={owner_pid.value}"

    def traced_target_is_valid(self, *, hwnd, process_id, point):
        log(f"target-valid-start point={point}")
        try:
            raw = self.bridge.run(_pointer_script(hwnd, process_id, point, send=False))
            log(f"target-valid-raw hwnd={hwnd} {raw}")
            log(f"target-valid-foreground {_describe_hwnd(int(raw['foreground']))}")
            log(f"target-valid-hit-root {_describe_hwnd(int(raw['hit_root']))}")
            result = bool(raw.get("valid")) and not bool(raw.get("sent"))
        except Exception as exc:
            log(f"target-valid-error {type(exc).__name__}: {exc}")
            raise
        log(f"target-valid-end result={result}")
        return result

    WindowsSendInputPointer.click = traced_click
    WindowsSendInputPointer.target_is_valid = traced_target_is_valid

    from finitact.stage1_extractors import EdgeRectangleRegionExtractor

    original_edge_extract = EdgeRectangleRegionExtractor.extract

    def traced_edge_extract(self, frame):
        import faulthandler

        log("edge-extract-start")
        stacks_path = trace.with_name("edge-extract-stacks.log")
        stacks_file = open(stacks_path, "w", encoding="utf-8", buffering=1)
        faulthandler.dump_traceback_later(4, repeat=True, file=stacks_file, exit=False)
        try:
            result = original_edge_extract(self, frame)
        except Exception as exc:
            log(f"edge-extract-error {type(exc).__name__}: {exc}")
            raise
        finally:
            faulthandler.cancel_dump_traceback_later()
            stacks_file.close()
        log(f"edge-extract-end regions={len(result)}")
        return result

    EdgeRectangleRegionExtractor.extract = traced_edge_extract

    from finitact.windows_screen_grounded import WindowsPrintWindowCapture

    original_capture = WindowsPrintWindowCapture.capture

    def traced_capture(self):
        log("capture-start")
        try:
            frame = original_capture(self)
        except Exception as exc:
            log(f"capture-error {type(exc).__name__}: {exc}")
            raise
        log(f"capture-end {frame.width}x{frame.height}")
        return frame

    WindowsPrintWindowCapture.capture = traced_capture

    import traceback

    from finitact.interaction_lease import SyntheticInputGuard

    original_guard_execute = SyntheticInputGuard.execute

    def traced_guard_execute(self, **kwargs):
        log("guard-execute-start")
        try:
            result = original_guard_execute(self, **kwargs)
        except Exception:
            log("guard-execute-error\n" + traceback.format_exc())
            raise
        log(f"guard-execute-end status={result.status} detail={result.detail}")
        return result

    SyntheticInputGuard.execute = traced_guard_execute

    from finitact.automation_indicator import AutomationIndicator

    original_indicator_show = AutomationIndicator.show

    def traced_show(self):
        log("indicator-show-start")
        start = time.monotonic()
        try:
            original_indicator_show(self)
        except Exception as exc:
            log(f"indicator-show-error elapsed={time.monotonic() - start:.3f} {type(exc).__name__}: {exc}")
            raise
        log(f"indicator-show-end elapsed={time.monotonic() - start:.3f}")

    AutomationIndicator.show = traced_show

    class OcrLabelProvider:
        def decide(self, request, *, attempts, call_id):
            matches = [c for c in request.candidates if TARGET_LABEL in c.label.upper()]
            log(
                f"provider candidates={len(request.candidates)} "
                f"labels={[c.label for c in request.candidates]} matches={len(matches)}"
            )
            attempts.append({"status": "confirmed", "call_id": call_id, "provider": "probe-ocr-label"})
            if len(matches) != 1:
                return Decision(terminal_reason="blocked")
            return Decision(choice=matches[0].id)

    verifier = WindowsOwnedWindowVerifier(
        WindowsOwnedWindowEnumerator(), target_pid, window_appeared(title_is(POPUP_TITLE))
    )
    state_home = Path(os.environ["XDG_STATE_HOME"])
    mcp_server.windows_coordinator = WindowsRunCoordinator(
        adapter_factory=windows_adapter_factory,
        decision_provider=OcrLabelProvider(),
        decision_cache=InMemoryDecisionCache(),
        verifier=verifier,
        ledger_path=state_home / "finitact" / "windows-runs.sqlite3",
    )

    calls_path = Path(os.environ["FINITACT_CLICK_CALLS_PATH"])

    def dump_calls() -> None:
        calls_path.write_text(json.dumps(click_calls), encoding="utf-8")

    import atexit

    atexit.register(dump_calls)
    log("server-ready")
    mcp_server.main()


def _result_record(result) -> dict:
    if result.structuredContent:
        return dict(result.structuredContent)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    raise RuntimeError("MCP tool returned no JSON result")


async def _probe() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    idle_before = _wait_for_idle(minimum_seconds=IDLE_MINIMUM_SECONDS, timeout_seconds=IDLE_POLL_TIMEOUT_SECONDS)
    print(f"idle precondition satisfied: {idle_before:.1f}s", file=sys.stderr, flush=True)

    with tempfile.TemporaryDirectory(prefix="finitact-screen-success-probe-") as temp_dir:
        temp = Path(temp_dir)
        trace_path = temp / "server-trace.log"
        calls_path = temp / "click-calls.json"
        target = subprocess.Popen(
            [sys.executable, __file__, "--target"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            identity = _read_json_line(target, timeout=10)
            print(f"target-ready {identity}", file=sys.stderr, flush=True)
            _force_foreground(identity["hwnd"])
            print("target-foregrounded", file=sys.stderr, flush=True)
            baseline = _list_top_level_titles(identity["pid"])

            child_env = dict(os.environ)
            child_env["FINITACT_TARGET_PID"] = str(identity["pid"])
            child_env["FINITACT_TRACE_PATH"] = str(trace_path)
            child_env["FINITACT_CLICK_CALLS_PATH"] = str(calls_path)
            child_env["XDG_STATE_HOME"] = str(temp / "state")
            server = StdioServerParameters(command=sys.executable, args=[__file__, "--server"], env=child_env)

            run_id = f"screen-success-click-{int(time.time())}"
            request = {
                    "run_id": run_id,
                    "target_id": f"screen:{identity['hwnd']}:{identity['pid']}",
                    "goals": [{"id": "g1", "goal": "Click the CLICK boxed region to open the popup."}],
                    "allowed_operations": ["click"],
                    "synthetic_input_allowed": True,
                    "exclusive_environment_ref": "preregistered-screen-success-probe",
                    "deadline_ms": 40000,
                    "action_budget": 1,
                    "provider_attempt_budget": 2,
                }
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    print("mcp-ready", file=sys.stderr, flush=True)
                    try:
                        result = _result_record(
                            await asyncio.wait_for(session.call_tool("run_windows", request), timeout=45)
                        )
                    except Exception:
                        if trace_path.exists():
                            print("--- server trace ---", file=sys.stderr, flush=True)
                            print(trace_path.read_text(encoding="utf-8"), file=sys.stderr, flush=True)
                        raise
                    print("run-returned", file=sys.stderr, flush=True)
                    journal = _result_record(await session.call_tool("get_run_journal", {"run_id": run_id}))
            if trace_path.exists():
                print("--- server trace ---", file=sys.stderr, flush=True)
                print(trace_path.read_text(encoding="utf-8"), file=sys.stderr, flush=True)

            idle_after_return = _seconds_idle()
            after = _list_top_level_titles(identity["pid"])
            appeared_titles = {title for _hwnd, title in after} - {title for _hwnd, title in baseline}
            independent_confirmation = POPUP_TITLE in appeared_titles

            click_calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []

            goal = result["goals"][0]
            passed = (
                result["status"] == "completed"
                and goal["termination_reason"] == "outcome_verified"
                and goal["mutation_state"] == "confirmed"
                and goal["outcome"] == "verified_success"
                and len(click_calls) == 1
                and click_calls[0]["confirmed"] is True
                and independent_confirmation
            )
            record = {
                "case_id": run_id,
                "passed": passed,
                "target": identity,
                "idle_seconds_before_run": idle_before,
                "idle_seconds_immediately_after_click": idle_after_return,
                "result": result,
                "journal": journal,
                "send_input_pointer_calls": click_calls,
                "independent_oracle_launcher_ctypes_enum_windows": {
                    "baseline_titles": [title for _hwnd, title in baseline],
                    "after_titles": [title for _hwnd, title in after],
                    "popup_confirmed": independent_confirmation,
                },
                "provider": "deterministic probe provider: the single candidate whose label reads CLICK",
                "extractor_override": None,
                "note": (
                    "Disposable Tk target, not the preregistered screen-unity-open-dropdown-001 case. "
                    "That case remains not_run pending a live Unity target for Phase I."
                ),
            }
            if not passed:
                raise RuntimeError(json.dumps(record, ensure_ascii=False, indent=2))
            return record
        finally:
            if target.poll() is None:
                target.terminate()
                try:
                    target.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    target.kill()
                    target.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", action="store_true")
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("this live probe requires Windows-native Python")
    if args.target:
        _target()
    elif args.server:
        _server()
    else:
        print(json.dumps(asyncio.run(_probe()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
