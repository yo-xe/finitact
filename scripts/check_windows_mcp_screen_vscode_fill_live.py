"""Bounded Windows-native MCP probe: screen-grounded fill into a VSCode editor, then Ctrl+S.

A disposable VSCode instance (isolated ``--user-data-dir`` and ``--extensions-dir``) opens one file
holding OLDVALUE. One ``run_windows(screen:...)`` call with ``allowed_operations=["fill", "key"]``
and action_budget=2 must replace the whole document (click on the OLDVALUE OCR region, Ctrl+A,
unicode input) and then press the allowlisted Ctrl+S. Success is the file on disk, read by the
server-side verifier and again by the launcher -- a channel independent of SendInput.

The text is multi-line Python with brackets and an indented line, so editor auto-indent and
auto-closing brackets are exercised as real editors apply them (plan screen-input-ops step 1).
The saved bytes are recorded even when they differ, since that difference is the finding.
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

from check_windows_mcp_screen_success_live import _force_foreground, _result_record, _wait_for_idle

IDLE_MINIMUM_SECONDS = 32.0
IDLE_POLL_TIMEOUT_SECONDS = 180.0
CODE_EXE = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe"
OLD_VALUE = "OLDVALUE"
MULTILINE_TEXT = "def f(x):\n    return [x, (1, 2)]  # 日本語\n"
# Auto-closing brackets only; windows-mcp Type also types newlines key by key, so auto-indent hits both.
SINGLE_LINE_TEXT = "x = [1, (2, 3)]  # 日本語"
NEW_TEXT = MULTILINE_TEXT
SETTINGS = {
    "workbench.startupEditor": "none",
    "security.workspace.trust.enabled": False,
    "update.mode": "none",
    "telemetry.telemetryLevel": "off",
    "workbench.tips.enabled": False,
    "extensions.ignoreRecommendations": True,
    "chat.disableAIFeatures": True,
    "window.restoreWindows": "none",
    "files.eol": "\n",
    "git.enabled": False,
}


def _normalized(path: Path) -> str:
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def _find_window(pid_title: str, *, timeout: float) -> tuple[int, int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found: list[tuple[int, int]] = []

        def callback(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                buffer = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buffer, 512)
                if pid_title in buffer.value and "Visual Studio Code" in buffer.value:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    found.append((hwnd, pid.value))
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if len(found) == 1:
            return found[0]
        time.sleep(0.5)
    raise TimeoutError(f"VSCode window for {pid_title} not found")


def _server() -> None:
    from finitact import mcp_server
    from finitact.contracts import Decision
    from finitact.decision_cache import InMemoryDecisionCache
    from finitact.runs import WindowsRunCoordinator
    from finitact.windows_adapter_router import windows_adapter_factory
    from finitact.windows_screen_grounded import WindowsSendInputPointer

    target_file = Path(os.environ["FINITACT_TARGET_FILE"])
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

    class ScriptedProvider:
        def decide(self, request, *, attempts, call_id):
            attempts.append({"status": "confirmed", "call_id": call_id, "provider": "probe-scripted"})
            filled = any(item.get("kind") == "fill" for item in request.history)
            if not filled:
                wanted = [c for c in request.candidates if c.operation == "fill" and "VALUE" in c.label]
            else:
                wanted = [c for c in request.candidates if c.operation == "key" and c.attributes.get("key") == "Ctrl+S"]
            log(f"provider filled={filled} candidates={len(request.candidates)} matches={len(wanted)} "
                f"labels={[c.label for c in request.candidates if c.operation == 'fill'][:40]}")
            return Decision(choice=wanted[0].id) if len(wanted) == 1 else Decision(terminal_reason="blocked")

    class FixedText:
        def generate(self, context, *, attempts, call_id, attempt_limit):
            return NEW_TEXT, {}

    def saved_verifier(_goal, _observation):
        content = _normalized(target_file)
        log(f"verify saved={content!r}")
        return True if content == NEW_TEXT else None

    state_home = Path(os.environ["XDG_STATE_HOME"])
    mcp_server.windows_coordinator = WindowsRunCoordinator(
        adapter_factory=windows_adapter_factory,
        decision_provider=ScriptedProvider(),
        decision_cache=InMemoryDecisionCache(),
        text_helper=FixedText(),
        verifier=saved_verifier,
        ledger_path=state_home / "finitact" / "windows-runs.sqlite3",
    )
    calls_path = Path(os.environ["FINITACT_CALLS_PATH"])

    import atexit

    atexit.register(lambda: calls_path.write_text(json.dumps(calls), encoding="utf-8"))
    mcp_server.main()


async def _probe() -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with tempfile.TemporaryDirectory(prefix="finitact-vscode-fill-") as temp_dir:
        temp = Path(temp_dir)
        user_dir = temp / "user-data"
        (user_dir / "User").mkdir(parents=True)
        (user_dir / "User" / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
        target_file = temp / "finitact_fill_probe.py"
        target_file.write_text(OLD_VALUE + "\n", encoding="utf-8")
        trace_path = temp / "server-trace.log"
        calls_path = temp / "calls.json"
        code = subprocess.Popen(
            [str(CODE_EXE), "--user-data-dir", str(user_dir), "--extensions-dir", str(temp / "ext"),
             "--new-window", "--disable-workspace-trust", "--skip-release-notes", str(target_file)]
        )
        try:
            hwnd, pid = _find_window(target_file.name, timeout=40)
            time.sleep(3)  # let the editor finish its first layout before the idle wait starts
            idle_before = _wait_for_idle(minimum_seconds=IDLE_MINIMUM_SECONDS, timeout_seconds=IDLE_POLL_TIMEOUT_SECONDS)
            _force_foreground(hwnd)
            child_env = dict(os.environ)
            child_env.update(
                FINITACT_TARGET_FILE=str(target_file),
                FINITACT_TRACE_PATH=str(trace_path),
                FINITACT_CALLS_PATH=str(calls_path),
                XDG_STATE_HOME=str(temp / "state"),
            )
            server = StdioServerParameters(command=sys.executable, args=[__file__, "--server", "--text", "single" if NEW_TEXT == SINGLE_LINE_TEXT else "multiline"], env=child_env)
            run_id = f"screen-vscode-fill-{int(time.time())}"
            request = {
                    "run_id": run_id,
                    "target_id": f"screen:{hwnd}:{pid}",
                    "goals": [{"id": "g1", "goal": "Replace the document with the given code and save it."}],
                    "allowed_operations": ["fill", "key"],
                    "synthetic_input_allowed": True,
                    "exclusive_environment_ref": "preregistered-screen-vscode-fill-probe",
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
            saved = _normalized(target_file)
            calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
            goal = result["goals"][0]
            return {
                "case_id": run_id,
                "passed": result["status"] == "completed" and goal["outcome"] == "verified_success" and saved == NEW_TEXT,
                "idle_seconds_before_run": idle_before,
                "elapsed_seconds": round(elapsed, 2),
                "result": result,
                "pointer_calls": calls,
                "independent_oracle_launcher_file_read": {"saved": saved, "expected": NEW_TEXT},
            }
        finally:
            subprocess.run(["taskkill", "/PID", str(code.pid), "/T", "/F"], capture_output=True)
            time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--text", choices=("multiline", "single"), default="multiline")
    args = parser.parse_args()
    global NEW_TEXT
    NEW_TEXT = SINGLE_LINE_TEXT if args.text == "single" else MULTILINE_TEXT
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
