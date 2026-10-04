"""Bounded Windows-native MCP probe: screen-grounded fill into Discord's message box, then Enter.

Targets the already-open Discord window on the private channel ``#test`` (yo-xe permitted, plan
screen-input-ops). One ``run_windows(screen:...)`` call with ``allowed_operations=["fill", "key"]``
and action_budget=2 must click the message box (the fill candidate whose OCR region lies inside the
box's UIA rectangle), paste a unique nonce and press Enter. Success is the nonce appearing as a posted
message in the channel's message list, read through Chromium's UIA tree -- a channel independent of
SendInput and of the OCR the run observes with. The launcher repeats the read and also requires the
message box to be empty again, which separates "sent" from "typed but still pending".
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from check_windows_mcp_screen_success_live import _force_foreground, _result_record, _wait_for_idle

IDLE_MINIMUM_SECONDS = 32.0
IDLE_POLL_TIMEOUT_SECONDS = 180.0
WINDOW_TITLE = "#test | esp32 - Discord"
MESSAGE_LIST_NAME = "testのメッセージ"

UIA_SCRIPT = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type @'
using System; using System.Runtime.InteropServices;
public static class FinitactDiscordRect {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
}
'@
$hwnd = [IntPtr]__HWND__
$root = [Windows.Automation.AutomationElement]::FromHandle($hwnd)
$tree = [Windows.Automation.TreeScope]::Descendants
$edit = $root.FindFirst($tree, (New-Object Windows.Automation.PropertyCondition(
  [Windows.Automation.AutomationElement]::ControlTypeProperty, [Windows.Automation.ControlType]::Edit)))
$list = $root.FindFirst($tree, (New-Object Windows.Automation.PropertyCondition(
  [Windows.Automation.AutomationElement]::NameProperty, '__LIST__')))
$texts = @()
if ($list) {
  foreach ($e in $list.FindAll($tree, [Windows.Automation.Condition]::TrueCondition)) {
    if ($e.Current.Name) { $texts += $e.Current.Name }
  }
}
$w = New-Object FinitactDiscordRect+RECT
[void][FinitactDiscordRect]::GetWindowRect($hwnd, [ref]$w)
$editOut = $null
if ($edit) {
  $b = $edit.Current.BoundingRectangle
  $value = ''
  try { $value = $edit.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value } catch {}
  $editOut = @{ name=$edit.Current.Name; left=[int]$b.Left - $w.Left; top=[int]$b.Top - $w.Top;
                width=[int]$b.Width; height=[int]$b.Height; value=$value }
}
@{ edit=$editOut; list_found=[bool]$list; texts=$texts } | ConvertTo-Json -Compress -Depth 4
"""


def _uia_state(hwnd: int) -> dict:
    script = UIA_SCRIPT.replace("__HWND__", str(hwnd)).replace("__LIST__", MESSAGE_LIST_NAME)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip())
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _posted(state: dict, nonce: str) -> bool:
    return any(nonce in text for text in state.get("texts", []))


def _find_window(title: str) -> tuple[int, int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[tuple[int, int]] = []

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buffer, 512)
            if buffer.value == title:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((hwnd, pid.value))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    if len(found) != 1:
        raise SystemExit(f"expected one window titled {title!r}, found {len(found)}")
    return found[0]


def _server() -> None:
    from finitact import mcp_server
    from finitact.contracts import Decision
    from finitact.decision_cache import InMemoryDecisionCache
    from finitact.runs import WindowsRunCoordinator
    from finitact.stage1_extractors import EDGE_CONTOUR_SOURCE
    from finitact.windows_adapter_router import windows_adapter_factory
    from finitact.windows_screen_grounded import WindowsSendInputPointer

    hwnd = int(os.environ["FINITACT_TARGET_HWND"])
    nonce = os.environ["FINITACT_NONCE"]
    box = json.loads(os.environ["FINITACT_EDIT_RECT"])
    trace = Path(os.environ["FINITACT_TRACE_PATH"])
    events: list[str] = []
    calls: list[dict] = []

    def log(event: str) -> None:
        events.append(f"{time.monotonic():.6f} {event}")
        trace.write_text("\n".join(events) + "\n", encoding="utf-8")

    def traced(name):
        original = getattr(WindowsSendInputPointer, name)

        def wrapper(self, **kwargs):
            shown = {key: value for key, value in kwargs.items() if key != "text"}
            result = original(self, **kwargs)
            calls.append({"method": name, **shown, "confirmed": result})
            log(f"{name} {shown} confirmed={result}")
            return result

        setattr(WindowsSendInputPointer, name, wrapper)

    for name in ("type_text", "press_key", "focus_is_valid", "target_is_valid"):
        traced(name)

    def inside_box(candidate) -> bool:
        left, top, width, height = candidate.attributes["rect"]
        x, y = left + width // 2, top + height // 2
        return box["left"] <= x < box["left"] + box["width"] and box["top"] <= y < box["top"] + box["height"]

    class ScriptedProvider:
        def decide(self, request, *, attempts, call_id):
            attempts.append({"status": "confirmed", "call_id": call_id, "provider": "probe-scripted"})
            filled = any(item.get("kind") == "fill" for item in request.history)
            if not filled:
                # The box also holds icon regions; the placeholder text region is the field itself.
                wanted = [c for c in request.candidates if c.operation == "fill" and inside_box(c)
                          and c.attributes.get("source") != EDGE_CONTOUR_SOURCE]
            else:
                wanted = [c for c in request.candidates if c.operation == "key" and c.attributes.get("key") == "Enter"]
            log(f"provider filled={filled} candidates={len(request.candidates)} matches={[c.label for c in wanted]}")
            return Decision(choice=wanted[0].id) if len(wanted) == 1 else Decision(terminal_reason="blocked")

    class FixedText:
        def generate(self, context, *, attempts, call_id, attempt_limit):
            log(f"text-helper field={context['field'].get('label')!r}")
            return nonce, {}

    def posted_verifier(_goal, _observation):
        state = _uia_state(hwnd)
        log(f"verify posted={_posted(state, nonce)} box={state['edit'] and state['edit']['value']!r}")
        return True if _posted(state, nonce) else None

    state_home = Path(os.environ["XDG_STATE_HOME"])
    mcp_server.windows_coordinator = WindowsRunCoordinator(
        adapter_factory=windows_adapter_factory,
        decision_provider=ScriptedProvider(),
        decision_cache=InMemoryDecisionCache(),
        text_helper=FixedText(),
        verifier=posted_verifier,
        ledger_path=state_home / "finitact" / "windows-runs.sqlite3",
    )
    calls_path = Path(os.environ["FINITACT_CALLS_PATH"])

    import atexit

    atexit.register(lambda: calls_path.write_text(json.dumps(calls), encoding="utf-8"))
    mcp_server.main()


async def _probe() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    hwnd, pid = _find_window(WINDOW_TITLE)
    nonce = f"finitact probe {int(time.time())} 日本語"
    before = _uia_state(hwnd)
    if not before["edit"] or not before["list_found"]:
        raise SystemExit(f"Discord UIA tree lacks the message box or list: {before}")
    if _posted(before, nonce):
        raise SystemExit("nonce already present before the run")
    with tempfile.TemporaryDirectory(prefix="finitact-discord-fill-") as temp_dir:
        temp = Path(temp_dir)
        trace_path = temp / "server-trace.log"
        calls_path = temp / "calls.json"
        idle_before = _wait_for_idle(minimum_seconds=IDLE_MINIMUM_SECONDS, timeout_seconds=IDLE_POLL_TIMEOUT_SECONDS)
        _force_foreground(hwnd)
        time.sleep(0.5)
        box = _uia_state(hwnd)["edit"]
        child_env = dict(os.environ)
        child_env.update(
            FINITACT_TARGET_HWND=str(hwnd),
            FINITACT_NONCE=nonce,
            FINITACT_EDIT_RECT=json.dumps(box),
            FINITACT_TRACE_PATH=str(trace_path),
            FINITACT_CALLS_PATH=str(calls_path),
            XDG_STATE_HOME=str(temp / "state"),
        )
        server = StdioServerParameters(command=sys.executable, args=[__file__, "--server"], env=child_env)
        run_id = f"screen-discord-fill-{int(time.time())}"
        request = {
                "run_id": run_id,
                "target_id": f"screen:{hwnd}:{pid}",
                "goals": [{"id": "g1", "goal": "Post the given message to the #test channel."}],
                "allowed_operations": ["fill", "key"],
                "synthetic_input_allowed": True,
                "exclusive_environment_ref": "preregistered-screen-discord-fill-probe",
                "deadline_ms": 90000,
                "action_budget": 2,
                "provider_attempt_budget": 8,
            }
        started = time.monotonic()
        async with stdio_client(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = _result_record(
                    await asyncio.wait_for(session.call_tool("run_windows", request), timeout=100)
                )
        elapsed = time.monotonic() - started
        time.sleep(1)
        trace_text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
        print("--- server trace ---\n" + trace_text, file=sys.stderr, flush=True)
        after = _uia_state(hwnd)
        calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
        goal = result["goals"][0]
        box_after = (after["edit"] or {}).get("value", "").strip("﻿\n ")
        return {
            "case_id": run_id,
            "passed": result["status"] == "completed" and goal["outcome"] == "verified_success"
            and _posted(after, nonce) and box_after == "",
            "idle_seconds_before_run": idle_before,
            "elapsed_seconds": round(elapsed, 2),
            "edit_rect_window_relative": box,
            "result": result,
            "pointer_calls": calls,
            "independent_oracle_launcher_uia": {"posted": _posted(after, nonce), "nonce": nonce, "box_after": box_after},
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("this live probe requires Windows-native Python")
    if args.server:
        _server()
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        record = asyncio.run(_probe())
        print(json.dumps(record, ensure_ascii=False, indent=2))
        if not record["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
