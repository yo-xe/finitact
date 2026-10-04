"""Bounded Windows-native MCP probe for screen-grounded drag (plan screen-input-ops step 3).

A disposable Tk root shows SOURCE CARD, a DECOY ZONE and a DROP ZONE. The target implements its own DnD:
a drag only starts after the pointer moved past a threshold while button 1 is held, and the drop lands on
whatever widget contains the release point. One ``run_windows(screen:...)`` call with
``allowed_operations=["drag"]`` runs a deterministic provider that picks the start reading SOURCE, then the
composite end reading DROP ZONE.

Passing needs the run's verified success (owned popup ``DROPPED:SOURCE CARD``), the target's own record of
a threshold-crossing drag released on the drop zone, and exactly one confirmed native drag.
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
import threading
import time
from ctypes import wintypes
from pathlib import Path

from check_windows_mcp_screen_success_live import (
    _force_foreground,
    _list_top_level_titles,
    _read_json_line,
    _result_record,
    _wait_for_idle,
)

IDLE_MINIMUM_SECONDS = 32.0
IDLE_POLL_TIMEOUT_SECONDS = 180.0
SOURCE_LABEL = "SOURCE CARD"
DROP_LABEL = "DROP ZONE"
SUCCESS_TITLE = f"DROPPED:{SOURCE_LABEL}"
DRAG_THRESHOLD_PX = 6


def _target(state_path: str, *, popup: bool = True, decorated: bool = False) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("Finitact disposable drag probe")
    if not decorated:
        root.overrideredirect(True)
    root.geometry("640x300+80+80")
    root.configure(bg="white")
    font = ("Segoe UI", 16, "bold")
    source = tk.Label(root, text=SOURCE_LABEL, font=font, bg="#dde8ff", relief="raised", bd=2, width=12, height=3)
    decoy = tk.Label(root, text="DECOY ZONE", font=font, bg="#f4f4f4", relief="groove", bd=2, width=12, height=3)
    drop = tk.Label(root, text=DROP_LABEL, font=font, bg="#e8ffe8", relief="groove", bd=2, width=12, height=3)
    source.place(x=24, y=90)
    decoy.place(x=232, y=20)
    drop.place(x=440, y=160)
    state = {"pid": os.getpid(), "presses": 0, "motions": 0, "started": False, "dropped_on": "", "value": ""}
    pressed: dict[str, int] = {}

    written = threading.Lock()

    def write_state() -> None:
        with written:
            Path(state_path).write_text(json.dumps(state), encoding="utf-8")

    def heartbeat() -> None:
        # The Phase I oracle rejects a state file older than 2 s as a dead writer; on the Tk thread a drag's
        # modal loop stalled it past that and one u1 trial was judged undecidable (public-repo plan).
        while True:
            write_state()
            time.sleep(0.5)

    def press(event) -> None:
        state["presses"] += 1
        pressed.update(x=event.x_root, y=event.y_root)
        write_state()

    def motion(event) -> None:
        state["motions"] += 1
        if abs(event.x_root - pressed["x"]) + abs(event.y_root - pressed["y"]) > DRAG_THRESHOLD_PX:
            state["started"] = True
        write_state()

    def release(event) -> None:
        if not state["started"]:
            write_state()
            return
        under = root.winfo_containing(event.x_root, event.y_root)
        name = under.cget("text") if isinstance(under, tk.Label) else ""
        state["dropped_on"] = name
        if name == DROP_LABEL:
            state["value"] = SOURCE_LABEL
            drop.configure(text=f"GOT {SOURCE_LABEL}")
            if popup:
                shown = tk.Toplevel(root)
                shown.title(SUCCESS_TITLE)
                shown.transient(root)
                shown.attributes("-toolwindow", True)
                shown.geometry("260x60+740+80")
                shown.update()
        write_state()

    source.bind("<ButtonPress-1>", press)
    source.bind("<B1-Motion>", motion)
    source.bind("<ButtonRelease-1>", release)
    root.update()
    threading.Thread(target=heartbeat, name="probe-heartbeat", daemon=True).start()
    GA_ROOT = 2
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.restype = wintypes.HWND
    top_level_hwnd = user32.GetAncestor(wintypes.HWND(root.winfo_id()), GA_ROOT)
    print(json.dumps({"hwnd": top_level_hwnd, "pid": os.getpid()}), flush=True)
    root.mainloop()


def _server() -> None:
    from finitact import mcp_server
    from finitact.contracts import Decision
    from finitact.decision_cache import InMemoryDecisionCache
    from finitact.runs import WindowsRunCoordinator
    from finitact.windows_adapter_router import windows_adapter_factory
    from finitact.windows_outcome_verifier import WindowsOwnedWindowVerifier, title_is, window_appeared
    from finitact.windows_screen_grounded import WindowsOwnedWindowEnumerator, WindowsSendInputPointer

    target_pid = int(os.environ["FINITACT_TARGET_PID"])
    trace = Path(os.environ["FINITACT_TRACE_PATH"])
    events: list[str] = []
    calls: list[dict] = []
    steps: list[dict] = []

    def log(event: str) -> None:
        events.append(f"{time.monotonic():.6f} {event}")
        trace.write_text("\n".join(events) + "\n", encoding="utf-8")

    def traced(name):
        original = getattr(WindowsSendInputPointer, name)

        def wrapper(self, **kwargs):
            log(f"{name}-start {kwargs}")
            result = original(self, **kwargs)
            calls.append({"method": name, **kwargs, "confirmed": result})
            log(f"{name}-end confirmed={result}")
            return result

        setattr(WindowsSendInputPointer, name, wrapper)

    for name in ("drag", "click", "target_is_valid"):
        traced(name)

    class ScriptedProvider:
        def decide(self, request, *, attempts, call_id):
            attempts.append({"status": "confirmed", "call_id": call_id, "provider": "probe-scripted"})
            drags = [c for c in request.candidates if c.operation == "drag"]
            starts = [c for c in drags if c.attributes.get("drag_phase") == "start" and "SOURCE" in c.label.upper()]
            ends = [c for c in drags if c.attributes.get("drag_phase") == "end" and "'DROP ZONE'" in c.label.upper()]
            steps.append({
                "labels": [c.label for c in request.candidates],
                "starts": [c.label for c in starts],
                "ends": [c.label for c in ends],
            })
            log(f"provider step={len(steps)} starts={steps[-1]['starts']} ends={steps[-1]['ends']}")
            if len(ends) == 1:
                return Decision(choice=ends[0].id)
            if len(starts) == 1 and not any(c.attributes.get("drag_phase") == "end" for c in drags):
                return Decision(choice=starts[0].id)
            return Decision(terminal_reason="blocked")

    verifier = WindowsOwnedWindowVerifier(
        WindowsOwnedWindowEnumerator(), target_pid, window_appeared(title_is(SUCCESS_TITLE))
    )
    state_home = Path(os.environ["XDG_STATE_HOME"])
    mcp_server.windows_coordinator = WindowsRunCoordinator(
        adapter_factory=windows_adapter_factory,
        decision_provider=ScriptedProvider(),
        decision_cache=InMemoryDecisionCache(),
        verifier=verifier,
        ledger_path=state_home / "finitact" / "windows-runs.sqlite3",
    )
    calls_path = Path(os.environ["FINITACT_CALLS_PATH"])

    import atexit

    atexit.register(lambda: calls_path.write_text(json.dumps({"calls": calls, "steps": steps}), encoding="utf-8"))
    log("server-ready")
    mcp_server.main()


async def _probe() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    idle_before = _wait_for_idle(minimum_seconds=IDLE_MINIMUM_SECONDS, timeout_seconds=IDLE_POLL_TIMEOUT_SECONDS)
    with tempfile.TemporaryDirectory(prefix="finitact-drag-probe-") as temp_dir:
        temp = Path(temp_dir)
        trace_path = temp / "server-trace.log"
        calls_path = temp / "calls.json"
        state_path = temp / "state.json"
        target = subprocess.Popen(
            [sys.executable, __file__, "--target", "--state", str(state_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            identity = _read_json_line(target, timeout=10)
            _force_foreground(identity["hwnd"])
            child_env = dict(os.environ)
            child_env.update(
                FINITACT_TARGET_PID=str(identity["pid"]),
                FINITACT_TRACE_PATH=str(trace_path),
                FINITACT_CALLS_PATH=str(calls_path),
                XDG_STATE_HOME=str(temp / "state"),
            )
            server = StdioServerParameters(command=sys.executable, args=[__file__, "--server"], env=child_env)
            run_id = f"screen-drag-{int(time.time())}"
            request = {
                    "run_id": run_id,
                    "target_id": f"screen:{identity['hwnd']}:{identity['pid']}",
                    "goals": [{"id": "g1", "goal": f"Drag '{SOURCE_LABEL}' onto '{DROP_LABEL}'."}],
                    "allowed_operations": ["drag"],
                    "synthetic_input_allowed": True,
                    "exclusive_environment_ref": "preregistered-screen-drag-probe",
                    "deadline_ms": 90000,
                    "action_budget": 3,
                    "provider_attempt_budget": 6,
                }
            started = time.monotonic()
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = _result_record(
                        await asyncio.wait_for(session.call_tool("run_windows", request), timeout=100)
                    )
            elapsed = time.monotonic() - started
            trace_text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
            print("--- server trace ---\n" + trace_text, file=sys.stderr, flush=True)
            titles = [title for _hwnd, title in _list_top_level_titles(identity["pid"])]
            recorded = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else {}
            calls, steps = recorded.get("calls", []), recorded.get("steps", [])
            drags = [call for call in calls if call["method"] == "drag"]
            target_state = json.loads(state_path.read_text(encoding="utf-8"))
            passed = (
                result["status"] == "completed"
                and result["goals"][0]["outcome"] == "verified_success"
                and len(drags) == 1 and drags[0]["confirmed"]
                and not any(call["method"] == "click" for call in calls)
                and target_state["presses"] == 1
                and target_state["started"]
                and target_state["dropped_on"] == DROP_LABEL
                and SUCCESS_TITLE in titles
            )
            return {
                "case_id": run_id,
                "passed": passed,
                "idle_seconds_before_run": idle_before,
                "elapsed_seconds": round(elapsed, 2),
                "result": result,
                "pointer_calls": calls,
                "provider_steps": steps,
                "independent_oracle": {"titles": titles, "target_state": target_state},
            }
        finally:
            if target.poll() is None:
                target.terminate()
                target.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", action="store_true")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--state")
    parser.add_argument("--no-popup", action="store_true")
    parser.add_argument("--decorated", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("this live probe requires Windows-native Python")
    if args.target:
        _target(args.state, popup=not args.no_popup, decorated=args.decorated)
    elif args.server:
        _server()
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        record = asyncio.run(_probe())
        print(json.dumps(record, ensure_ascii=False, indent=2))
        if not record["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
