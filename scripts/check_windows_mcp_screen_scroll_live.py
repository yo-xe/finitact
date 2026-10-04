"""Bounded Windows-native MCP probe for screen-grounded scroll (plan screen-input-ops step 2).

A disposable borderless Tk root holds one Listbox of 60 rows, 12 visible. One ``run_windows(screen:...)``
call with ``allowed_operations=["click", "scroll"]`` runs a deterministic provider:

- ``find``: row 45 reads TARGET ROW. The provider clicks a candidate reading TARGET once one is offered,
  otherwise the single ``scroll down`` candidate. Selecting the row opens an owned popup titled
  ``SELECTED:TARGET ROW``, judged by ``WindowsOwnedWindowVerifier`` and again by the launcher's
  ``EnumWindows``.
- ``two`` / ``two-left`` (target only): two 60-row lists side by side; only the right (``two``) or left
  (``two-left``) one holds TARGET ROW, so scrolling the other never reaches it. ``selected`` is the target
  list's selection and ``other_selected`` the other list's.
- ``end``: no target. The provider scrolls down while a down candidate is offered, then blocks. Passing
  requires the down candidate to vanish only after the list reached its end, which the target reports
  independently as its ``yview`` bottom fraction.
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

from check_windows_mcp_screen_success_live import (
    _force_foreground,
    _list_top_level_titles,
    _read_json_line,
    _result_record,
    _wait_for_idle,
)

IDLE_MINIMUM_SECONDS = 32.0
IDLE_POLL_TIMEOUT_SECONDS = 180.0
ROWS = 60
TARGET_INDEX = 44
TARGET_LABEL = "TARGET ROW"
DECOY_INDEX, DECOY_LABEL = 29, "TARGET ROWS"
SUCCESS_TITLE = f"SELECTED:{TARGET_LABEL}"


def _rows(case: str) -> list[str]:
    rows = [f"Item {index + 1:02d} entry" for index in range(ROWS)]
    if case == "find":
        rows[TARGET_INDEX] = TARGET_LABEL
    return rows


def _target(
    case: str, yview_path: str, *, state_path: str | None = None, popup: bool = True, decorated: bool = False
) -> None:
    """``state_path`` (Phase I) gets the selection as well; without the popup the list keeps the foreground."""

    import tkinter as tk

    root = tk.Tk()
    root.title("Finitact disposable scroll probe")
    if not decorated:
        # windows-mcp lists only framed app windows, so the Phase I comparison needs a decorated target.
        root.overrideredirect(True)
    two = case in ("two", "two-left", "two-left-decoy")
    root.geometry("620x420+80+80" if two else "300x420+80+80")
    columns = [[f"Note {index + 1:02d} text" for index in range(ROWS)], _rows("end")] if two else [_rows(case)]
    if two:
        columns[0 if case.startswith("two-left") else 1][TARGET_INDEX] = TARGET_LABEL
    if case == "two-left-decoy":
        # A near-name row the provider's preferred wrong list reveals only on a second scroll (EXP-0013 follow-up).
        columns[1][DECOY_INDEX] = DECOY_LABEL
    lists = []
    for rows in columns:
        box = tk.Listbox(root, font=("Segoe UI", 14), height=12, activestyle="none", exportselection=False)
        for row in rows:
            box.insert(tk.END, row)
        box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=16, pady=16)
        lists.append(box)
    listbox = lists[0] if case.startswith("two-left") else lists[-1]
    others = [box for box in lists if box is not listbox]

    def selected(_event) -> None:
        chosen = listbox.curselection()
        if not chosen or not popup:
            return
        shown = tk.Toplevel(root)
        shown.title(f"SELECTED:{listbox.get(chosen[0])}")
        shown.transient(root)
        shown.attributes("-toolwindow", True)
        shown.geometry("260x60+420+80")
        shown.update()

    def report() -> None:
        Path(yview_path).write_text(json.dumps(listbox.yview()), encoding="utf-8")
        if state_path:
            chosen = listbox.curselection()
            state = {"pid": os.getpid(), "yview": listbox.yview(), "selected": listbox.get(chosen[0]) if chosen else "",
                     "other_selected": [box.get(index) for box in others for index in box.curselection()]}
            Path(state_path).write_text(json.dumps(state), encoding="utf-8")
        root.after(100, report)

    listbox.bind("<<ListboxSelect>>", selected)
    root.update()
    report()
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
    yview_path = Path(os.environ["FINITACT_YVIEW_PATH"])
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

    for name in ("scroll", "click", "target_is_valid"):
        traced(name)

    class ScriptedProvider:
        def decide(self, request, *, attempts, call_id):
            attempts.append({"status": "confirmed", "call_id": call_id, "provider": "probe-scripted"})
            scrolls = [c for c in request.candidates if c.operation == "scroll"]
            targets = [c for c in request.candidates if c.operation == "click" and "TARGET" in c.label.upper()]
            downs = [c for c in scrolls if c.attributes.get("direction") == "down"]
            steps.append({
                "yview": json.loads(yview_path.read_text(encoding="utf-8")),
                "scrolls": [(c.label, c.attributes.get("direction"), c.attributes.get("notches")) for c in scrolls],
                "targets": [c.label for c in targets],
            })
            log(f"provider step={len(steps)} {steps[-1]}")
            if len(targets) == 1:
                return Decision(choice=targets[0].id)
            if len(downs) == 1:
                return Decision(choice=downs[0].id)
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


async def _probe(case: str) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    idle_before = _wait_for_idle(minimum_seconds=IDLE_MINIMUM_SECONDS, timeout_seconds=IDLE_POLL_TIMEOUT_SECONDS)
    with tempfile.TemporaryDirectory(prefix="finitact-scroll-probe-") as temp_dir:
        temp = Path(temp_dir)
        trace_path = temp / "server-trace.log"
        calls_path = temp / "calls.json"
        yview_path = temp / "yview.json"
        target = subprocess.Popen(
            [sys.executable, __file__, "--target", "--case", case, "--yview", str(yview_path)],
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
                FINITACT_YVIEW_PATH=str(yview_path),
                XDG_STATE_HOME=str(temp / "state"),
            )
            server = StdioServerParameters(command=sys.executable, args=[__file__, "--server"], env=child_env)
            run_id = f"screen-scroll-{case}-{int(time.time())}"
            goal = (
                f"Scroll the list until '{TARGET_LABEL}' is visible and select it."
                if case == "find"
                else "Scroll the list down to its last row."
            )
            request = {
                    "run_id": run_id,
                    "target_id": f"screen:{identity['hwnd']}:{identity['pid']}",
                    "goals": [{"id": "g1", "goal": goal}],
                    "allowed_operations": ["click", "scroll"],
                    "synthetic_input_allowed": True,
                    "exclusive_environment_ref": "preregistered-screen-scroll-probe",
                    "deadline_ms": 90000,
                    "action_budget": 10,
                    "provider_attempt_budget": 12,
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
            scrolls = [call for call in calls if call["method"] == "scroll"]
            final_yview = json.loads(yview_path.read_text(encoding="utf-8"))
            goal_record = result["goals"][0]
            if case == "find":
                passed = (
                    result["status"] == "completed"
                    and goal_record["outcome"] == "verified_success"
                    and scrolls and all(call["confirmed"] and call["notches"] < 0 for call in scrolls)
                    and SUCCESS_TITLE in titles
                )
            else:
                last = steps[-1] if steps else {}
                passed = (
                    bool(scrolls)
                    and all(call["confirmed"] and call["notches"] < 0 for call in scrolls)
                    and final_yview[1] == 1.0
                    and [direction for _label, direction, _n in last.get("scrolls", [])] == ["up"]
                    # The down candidate must survive every step before the end was reached.
                    and all(
                        any(direction == "down" for _label, direction, _n in step["scrolls"])
                        for step in steps[:-1]
                    )
                )
            return {
                "case_id": run_id,
                "case": case,
                "passed": passed,
                "idle_seconds_before_run": idle_before,
                "elapsed_seconds": round(elapsed, 2),
                "result": result,
                "pointer_calls": calls,
                "provider_steps": steps,
                "independent_oracle": {"titles": titles, "final_yview": final_yview},
            }
        finally:
            if target.poll() is None:
                target.terminate()
                target.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", action="store_true")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--case", choices=("find", "end", "two", "two-left", "two-left-decoy"), default="find")
    parser.add_argument("--yview")
    parser.add_argument("--state")
    parser.add_argument("--no-popup", action="store_true")
    parser.add_argument("--decorated", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("this live probe requires Windows-native Python")
    if args.target:
        _target(args.case, args.yview, state_path=args.state, popup=not args.no_popup, decorated=args.decorated)
    elif args.server:
        _server()
    elif args.case.startswith("two"):
        raise SystemExit("the two-list case is only a --target for experiment_scroll_common_live.py")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        record = asyncio.run(_probe(args.case))
        print(json.dumps(record, ensure_ascii=False, indent=2))
        if not record["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
