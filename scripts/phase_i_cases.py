"""Phase I case setups shared by the scripted oracle check and the system comparison runner.

Each ``prepare(case_id, probe)`` resolves a target immediately before the run (manifest
``rules.target_binding``), brings it to the registered initial_state, yields the external oracle with a
scripted drive, and restores or closes the target afterwards. Notepad, Calculator and Blender are
launched disposable; Unity attaches to the running evaluation project (ADR-0015 decision 5).
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
BLENDER_MENU_ITEMS = ("Edit Mode", "Sculpt Mode", "Vertex Paint", "Weight Paint")
NOTEPAD_TEXT = "FINITACT-UIA-001"
UNITY_GOAL_VALUE = "Full HD (1920x1080)"
UNITY_RESET_VALUE = "16:9 Aspect"
UNITY_POPUP_TITLE = "UnityEditor.PopupWindow"  # the hover tooltip shares the class, not the title
VSCODE_OLD_TEXT = "OLDVALUE"
VSCODE_NEW_TEXT = 'value = "Finitact 日本語"'
TK_SCROLL_TARGET = "TARGET ROW"
TK_DRAG_SOURCE = "SOURCE CARD"
TK_DRAG_DROP = "DROP ZONE"


@dataclass(frozen=True)
class PreparedCase:
    case_id: str
    target: Any  # WindowTarget
    target_record: dict
    oracle: Any
    scripted_drive: Callable[[], Any]
    settle_s: float = 0.0
    app_feed: str | None = None  # EXP-0015: the target's own state feed, when the setup launched one


def _top_windows(probe, match) -> list[dict]:
    user32 = probe._user32
    found: list[dict] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            class_name = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_name, 256)
            window = {"hwnd": int(hwnd), "class_name": class_name.value, "title": probe.title(hwnd)}
            if match(window):
                found.append(window)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return found


def _wait_new_windows(probe, match, before: set[int], *, timeout_s: float) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        new = [window for window in _top_windows(probe, match) if window["hwnd"] not in before]
        if new:
            time.sleep(1.0)  # let restored sibling windows appear too
            return [window for window in _top_windows(probe, match) if window["hwnd"] not in before]
        time.sleep(0.2)
    raise SystemExit("target window did not appear")


def _close(hwnd: int) -> None:
    ctypes.WinDLL("user32").PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)


def _click_label(probe, extractor, target, label: str, *, pick=min, popup: int | None = None) -> None:
    """Script-side delivery through Finitact's own checked pointer (foreground and hit-test).

    With ``popup``, the label is found and clicked inside that owned popup; forcing the root to the
    foreground would close it (Unity, BUG-0016), so the popup is only allowed as foreground instead.
    """

    from check_windows_mcp_screen_success_live import _force_foreground
    from finitact.windows_evaluation_oracles import normalize_label
    from finitact.windows_screen_grounded import WindowsSendInputPointer

    hwnd = popup or target.hwnd
    wanted = normalize_label(label)
    regions = [region for region in extractor.extract(probe.capture(hwnd)) if normalize_label(region.label) == wanted]
    if not regions:
        raise RuntimeError(f"{label!r} not recognized in HWND {hwnd}")
    x, y, width, height = pick(regions, key=lambda region: region.rect[1]).rect
    if popup is None:
        _force_foreground(target.hwnd)
    if not WindowsSendInputPointer().click(
        hwnd=hwnd,
        process_id=target.process_id,
        point=(x + width // 2, y + height // 2),
        foreground_hwnds=(target.hwnd, popup) if popup else None,
    ):
        raise RuntimeError(f"click on {label!r} was not delivered")


def _press_escape() -> None:
    user32 = ctypes.WinDLL("user32")
    user32.keybd_event(0x1B, 0, 0, 0)
    user32.keybd_event(0x1B, 0, 0x0002, 0)


def _click_title_bar(target, popup: int) -> None:
    from finitact.windows_screen_grounded import WindowsSendInputPointer

    rect = wintypes.RECT()
    ctypes.WinDLL("user32").GetWindowRect(wintypes.HWND(target.hwnd), ctypes.byref(rect))
    # 16px down clears the -8px frame of a maximized window and stays above the menu bar otherwise.
    if not WindowsSendInputPointer().click(
        hwnd=target.hwnd,
        process_id=target.process_id,
        point=((rect.right - rect.left) // 2, 16),
        foreground_hwnds=(target.hwnd, popup),
    ):
        raise RuntimeError("click on the Unity title bar was not delivered")


@contextmanager
def _notepad(probe) -> Iterator[PreparedCase]:
    from finitact.windows_evaluation_oracles import ValueOracle, WindowTarget, control_text, sole_child

    before = {window["hwnd"] for window in _top_windows(probe, lambda w: w["class_name"] == "Notepad")}
    subprocess.Popen(["notepad.exe"])
    windows = _wait_new_windows(probe, lambda w: w["class_name"] == "Notepad", before, timeout_s=10)
    try:
        # Win11 Notepad restores earlier session windows beside the new one; any empty one is disposable.
        for window in windows:
            control = sole_child(probe, window["hwnd"], "RichEditD2DPT")
            if probe.window_text(control) == "":
                break
        else:
            raise SystemExit("no empty Notepad document among the new windows")
        target = WindowTarget(window["hwnd"], probe.owner_pid(window["hwnd"]))
        buffer = ctypes.create_unicode_buffer(NOTEPAD_TEXT)
        yield PreparedCase(
            case_id="uia-notepad-fill-001",
            target=target,
            target_record={"hwnd": target.hwnd, "pid": target.process_id, "control_hwnd": control},
            oracle=ValueOracle(target, control_text(control), NOTEPAD_TEXT, initial="", probe=probe),
            scripted_drive=lambda: ctypes.WinDLL("user32").SendMessageW(wintypes.HWND(control), 0x000C, 0, buffer),
        )
        ctypes.WinDLL("user32").SendMessageW(wintypes.HWND(control), 0x000C, 0, ctypes.create_unicode_buffer(""))
    finally:
        for window in windows:
            _close(window["hwnd"])


@contextmanager
def _calculator(probe) -> Iterator[PreparedCase]:
    from finitact.stage1_extractors import default_ocr_extractor
    from finitact.windows_evaluation_oracles import ValueOracle, WindowTarget, automation_id_number

    is_calc = lambda w: w["class_name"] == "ApplicationFrameWindow" and w["title"] in ("電卓", "Calculator")
    before = {window["hwnd"] for window in _top_windows(probe, is_calc)}
    subprocess.Popen(["calc.exe"])
    window = _wait_new_windows(probe, is_calc, before, timeout_s=15)[0]
    time.sleep(2.0)
    target = WindowTarget(window["hwnd"], probe.owner_pid(window["hwnd"]))
    driver_ocr = default_ocr_extractor()
    try:
        yield PreparedCase(
            case_id="uia-calculator-digit-001",
            target=target,
            target_record={"hwnd": target.hwnd, "pid": target.process_id},
            oracle=ValueOracle(
                target,
                automation_id_number("CalculatorResults"),
                "7", initial="0", probe=probe,
            ),
            scripted_drive=lambda: _click_label(probe, driver_ocr, target, "7"),
            settle_s=1.0,
        )
    finally:
        _close(target.hwnd)


@contextmanager
def _blender(probe, case_id: str, app_feed: bool = False) -> Iterator[PreparedCase]:
    from finitact.stage1_extractors import default_ocr_extractor
    from finitact.windows_evaluation_oracles import (
        PopupSelectOracle,
        ValueOracle,
        WindowTarget,
        menu_items_visible,
        target_state,
    )

    state_path = os.path.join(tempfile.mkdtemp(prefix="finitact-blender-"), "state.json")
    writer = Path(__file__).with_name("blender_state_writer.py")
    command = [BLENDER, "--factory-startup", "--python", str(writer), "--", state_path]
    feed_path = None
    if app_feed:
        feed_path = os.path.join(os.path.dirname(state_path), "feed.json")
        feed = Path(__file__).with_name("blender_app_feed.py")
        command = [*command[:4], "--python", str(feed), "--", state_path, feed_path]
    blender = subprocess.Popen(command)
    try:
        deadline = time.monotonic() + 60
        while not os.path.exists(state_path) and time.monotonic() < deadline:
            time.sleep(0.5)
        windows = _top_windows(probe, lambda w: w["class_name"] == "GHOST_WindowClass")
        windows = [window for window in windows if probe.owner_pid(window["hwnd"]) == blender.pid]
        if len(windows) != 1:
            raise SystemExit(f"expected one Blender window for PID {blender.pid}, found {len(windows)}")
        target = WindowTarget(windows[0]["hwnd"], blender.pid)
        time.sleep(2.0)
        driver_ocr = default_ocr_extractor()
        record = {"hwnd": target.hwnd, "pid": target.process_id}

        if case_id == "screen-blender-select-mode-001":

            def select_edit_mode():
                _click_label(probe, driver_ocr, target, "Object Mode")
                time.sleep(3.0)  # the menu stays open about as long as a provider call between actions
                _click_label(probe, driver_ocr, target, "Edit Mode")
                time.sleep(0.5)

            yield PreparedCase(
                case_id=case_id,
                target=target,
                target_record=record,
                oracle=PopupSelectOracle(
                    target,
                    menu_items_visible(BLENDER_MENU_ITEMS, default_ocr_extractor(), min_visible=3),
                    target_state(state_path, lambda state: state["active_object_mode"]),
                    "EDIT",
                    probe=probe,
                ),
                scripted_drive=select_edit_mode,
                app_feed=feed_path,
            )
        else:
            yield PreparedCase(
                case_id=case_id,
                target=target,
                target_record=record,
                oracle=ValueOracle(
                    target, target_state(state_path, lambda state: state["workspaces"][0]), "Shading",
                    initial="Layout", probe=probe,
                ),
                scripted_drive=lambda: _click_label(probe, driver_ocr, target, "Shading"),
                settle_s=1.0,
                app_feed=feed_path,
            )
    finally:
        blender.kill()


def _foreground_attached(target) -> None:
    """An attached editor is wherever the operator left it; a freshly launched Blender starts in front.
    Finitact's delivery check requires the target in front and it never raises windows itself, so the
    initial state puts it there for both systems."""

    from check_windows_mcp_screen_success_live import _force_foreground

    _force_foreground(target.hwnd)


@contextmanager
def _unity(probe, case_id: str) -> Iterator[PreparedCase]:
    """Attached to the running project: the select case starts away from Full HD and every case ends
    with the popup closed, so the editor is left as a later run expects to find it."""

    from finitact.stage1_extractors import default_ocr_extractor
    from finitact.windows_evaluation_oracles import (
        PopupOpensOracle,
        PopupSelectOracle,
        WindowTarget,
        normalize_label,
        ocr_value_right_of,
        same_pid_windows,
        title_is,
    )

    windows = _top_windows(probe, lambda w: w["class_name"] == "UnityContainerWndClass" and " - Unity " in w["title"])
    if len(windows) != 1:
        raise SystemExit(f"expected one Unity Editor window, found {len(windows)}")
    target = WindowTarget(windows[0]["hwnd"], probe.owner_pid(windows[0]["hwnd"]))
    driver_ocr = default_ocr_extractor()
    # PP-OCR, not Windows OCR: the latter reads the dropdown arrow as a glyph glued to the value.
    read_value = ocr_value_right_of("Display 1", default_ocr_extractor())
    popups = same_pid_windows(title_is(UNITY_POPUP_TITLE))

    def wait_popups(opened: bool, timeout_s: float) -> list[dict]:
        deadline = time.monotonic() + timeout_s
        while bool(found := popups(probe, target)) != opened and time.monotonic() < deadline:
            time.sleep(0.1)
        return found

    def close_popups() -> None:
        if open_now := popups(probe, target):
            _press_escape()
            if wait_popups(False, 3):
                # The popup can ignore Escape entirely (even a posted WM_KEYDOWN); a click on the editor's own
                # title bar deactivates and closes it without touching any setting.
                _click_title_bar(target, open_now[0]["hwnd"])
                if wait_popups(False, 3):
                    raise SystemExit("Unity resolution popup did not close; stop before the next run")

    def open_popup(current: str) -> int:
        _click_label(probe, driver_ocr, target, current)
        opened = wait_popups(True, 5)
        if len(opened) != 1:
            raise RuntimeError(f"expected one resolution popup, found {len(opened)}")
        return opened[0]["hwnd"]

    def select(current: str, choice: str) -> None:
        popup = open_popup(current)
        time.sleep(3.0)  # the popup stays open about as long as a provider call between actions
        _click_label(probe, driver_ocr, target, choice, popup=popup)
        time.sleep(0.5)

    close_popups()
    current = read_value(probe, target)
    record = {"hwnd": target.hwnd, "pid": target.process_id}
    try:
        if case_id == "screen-unity-open-dropdown-001":
            # The shown value decides whether Jev answers directly or needs a pick (BUG-0027).
            if current != normalize_label(UNITY_GOAL_VALUE):
                select(current, UNITY_GOAL_VALUE)
                current = normalize_label(UNITY_GOAL_VALUE)
            _foreground_attached(target)
            yield PreparedCase(
                case_id=case_id,
                target=target,
                target_record=record,
                oracle=PopupOpensOracle(target, popups, probe=probe),
                scripted_drive=lambda: open_popup(current),
                settle_s=1.0,
            )
        else:
            if current == normalize_label(UNITY_GOAL_VALUE):
                select(UNITY_GOAL_VALUE, UNITY_RESET_VALUE)
            _foreground_attached(target)
            yield PreparedCase(
                case_id=case_id,
                target=target,
                target_record=record,
                oracle=PopupSelectOracle(target, popups, read_value, normalize_label(UNITY_GOAL_VALUE), probe=probe),
                scripted_drive=lambda: select(UNITY_RESET_VALUE, UNITY_GOAL_VALUE),
                settle_s=1.0,
            )
    finally:
        close_popups()


@contextmanager
def _vscode(probe) -> Iterator[PreparedCase]:
    """A disposable VSCode (isolated user data and extensions) with one file holding OLDVALUE."""

    from check_windows_mcp_screen_success_live import _force_foreground
    from check_windows_mcp_screen_vscode_fill_live import CODE_EXE, SETTINGS
    from finitact.stage1_extractors import default_ocr_extractor
    from finitact.windows_evaluation_oracles import ValueOracle, WindowTarget, normalize_label, saved_text
    from finitact.windows_screen_grounded import WindowsSendInputPointer

    temp = Path(tempfile.mkdtemp(prefix="finitact-phase-i-vscode-"))
    (temp / "user-data" / "User").mkdir(parents=True)
    (temp / "user-data" / "User" / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
    document = temp / "finitact_phase_i.py"
    document.write_text(VSCODE_OLD_TEXT + "\n", encoding="utf-8")
    code = subprocess.Popen(
        [CODE_EXE, "--user-data-dir", str(temp / "user-data"), "--extensions-dir", str(temp / "ext"),
         "--new-window", "--disable-workspace-trust", "--skip-release-notes", str(document)]
    )
    try:
        is_window = lambda w: w["title"].startswith(document.name) and "Visual Studio Code" in w["title"]
        window = _wait_new_windows(probe, is_window, set(), timeout_s=40)[0]
        time.sleep(3.0)  # the first editor layout settles after the title appears
        target = WindowTarget(window["hwnd"], probe.owner_pid(window["hwnd"]))
        _force_foreground(target.hwnd)
        driver_ocr = default_ocr_extractor()

        def fill_and_save():
            wanted = normalize_label(VSCODE_OLD_TEXT)
            regions = [r for r in driver_ocr.extract(probe.capture(target.hwnd)) if normalize_label(r.label) == wanted]
            if len(regions) != 1:
                raise RuntimeError(f"expected one {VSCODE_OLD_TEXT} region, found {len(regions)}")
            x, y, width, height = regions[0].rect
            pointer = WindowsSendInputPointer()
            if not pointer.type_text(
                hwnd=target.hwnd, process_id=target.process_id, point=(x + width // 2, y + height // 2),
                text=VSCODE_NEW_TEXT, foreground_hwnds=(target.hwnd,),
            ):
                raise RuntimeError("fill was not delivered")
            if not pointer.press_key(
                hwnd=target.hwnd, process_id=target.process_id, key="Ctrl+S", foreground_hwnds=(target.hwnd,)
            ):
                raise RuntimeError("Ctrl+S was not delivered")

        yield PreparedCase(
            case_id="screen-vscode-fill-save-001",
            target=target,
            target_record={"hwnd": target.hwnd, "pid": target.process_id, "document": str(document)},
            oracle=ValueOracle(target, saved_text(str(document)), VSCODE_NEW_TEXT, initial=VSCODE_OLD_TEXT, probe=probe),
            scripted_drive=fill_and_save,
            settle_s=1.0,
        )
    finally:
        subprocess.run(["taskkill", "/PID", str(code.pid), "/T", "/F"], capture_output=True)


@contextmanager
def _tk_scroll(probe) -> Iterator[PreparedCase]:
    """The disposable 60-row Tk list of the scroll probe; TARGET ROW (row 45) starts below the visible 12."""

    from check_windows_mcp_screen_success_live import _force_foreground, _read_json_line
    from finitact.stage1_extractors import default_ocr_extractor
    from finitact.windows_evaluation_oracles import ValueOracle, WindowTarget, normalize_label, target_state
    from finitact.windows_screen_grounded import WindowsSendInputPointer

    temp = Path(tempfile.mkdtemp(prefix="finitact-phase-i-tk-scroll-"))
    state_path = temp / "state.json"
    script = Path(__file__).with_name("check_windows_mcp_screen_scroll_live.py")
    tk_list = subprocess.Popen(
        [sys.executable, str(script), "--target", "--case", "find", "--no-popup", "--decorated",
         "--yview", str(temp / "yview.json"), "--state", str(state_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        identity = _read_json_line(tk_list, timeout=10)
        target = WindowTarget(identity["hwnd"], identity["pid"])
        deadline = time.monotonic() + 5
        while not state_path.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        _force_foreground(target.hwnd)
        driver_ocr = default_ocr_extractor()

        def scroll_and_select():
            for _ in range(12):
                if any(
                    normalize_label(region.label) == normalize_label(TK_SCROLL_TARGET)
                    for region in driver_ocr.extract(probe.capture(target.hwnd))
                ):
                    _click_label(probe, driver_ocr, target, TK_SCROLL_TARGET)
                    return
                if not WindowsSendInputPointer().scroll(
                    hwnd=target.hwnd, process_id=target.process_id, point=(150, 210), notches=-2
                ):
                    raise RuntimeError("scroll was not delivered")
                time.sleep(0.5)
            raise RuntimeError(f"{TK_SCROLL_TARGET!r} never became visible")

        yield PreparedCase(
            case_id="screen-tk-scroll-select-001",
            target=target,
            target_record={"hwnd": target.hwnd, "pid": target.process_id, "state": str(state_path)},
            oracle=ValueOracle(
                target, target_state(str(state_path), lambda state: state["selected"]), TK_SCROLL_TARGET,
                initial="", probe=probe,
            ),
            scripted_drive=scroll_and_select,
            settle_s=1.0,
        )
    finally:
        if tk_list.poll() is None:
            tk_list.terminate()
            tk_list.wait(timeout=5)


@contextmanager
def _tk_drag(probe) -> Iterator[PreparedCase]:
    """The disposable Tk DnD of the drag probe: its own threshold drag records where SOURCE CARD was released."""

    from check_windows_mcp_screen_success_live import _force_foreground, _read_json_line
    from finitact.stage1_extractors import default_ocr_extractor
    from finitact.windows_evaluation_oracles import ValueOracle, WindowTarget, normalize_label, target_state
    from finitact.windows_screen_grounded import WindowsSendInputPointer

    temp = Path(tempfile.mkdtemp(prefix="finitact-phase-i-tk-drag-"))
    state_path = temp / "state.json"
    script = Path(__file__).with_name("check_windows_mcp_screen_drag_live.py")
    tk_dnd = subprocess.Popen(
        [sys.executable, str(script), "--target", "--no-popup", "--decorated", "--state", str(state_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        identity = _read_json_line(tk_dnd, timeout=10)
        target = WindowTarget(identity["hwnd"], identity["pid"])
        deadline = time.monotonic() + 5
        while not state_path.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        _force_foreground(target.hwnd)
        driver_ocr = default_ocr_extractor()

        def drag_source_to_drop():
            regions = driver_ocr.extract(probe.capture(target.hwnd))

            def center(label: str) -> tuple[int, int]:
                found = [region for region in regions if normalize_label(region.label) == normalize_label(label)]
                if len(found) != 1:
                    raise RuntimeError(f"{label!r} recognized {len(found)} times in HWND {target.hwnd}")
                x, y, width, height = found[0].rect
                return x + width // 2, y + height // 2

            _force_foreground(target.hwnd)
            if not WindowsSendInputPointer().drag(
                hwnd=target.hwnd, process_id=target.process_id,
                start=center(TK_DRAG_SOURCE), end=center(TK_DRAG_DROP),
            ):
                raise RuntimeError("drag was not delivered")

        yield PreparedCase(
            case_id="screen-tk-drag-drop-001",
            target=target,
            target_record={"hwnd": target.hwnd, "pid": target.process_id, "state": str(state_path)},
            oracle=ValueOracle(
                target, target_state(str(state_path), lambda state: state["value"]), TK_DRAG_SOURCE,
                initial="", probe=probe,
            ),
            scripted_drive=drag_source_to_drop,
            settle_s=1.0,
        )
    finally:
        if tk_dnd.poll() is None:
            tk_dnd.terminate()
            tk_dnd.wait(timeout=5)


CASE_IDS = (
    "uia-notepad-fill-001",
    "uia-calculator-digit-001",
    "screen-blender-workspace-001",
    "screen-blender-select-mode-001",
    "screen-unity-open-dropdown-001",
    "screen-unity-select-resolution-001",
    "screen-vscode-fill-save-001",
    "screen-tk-scroll-select-001",
    "screen-tk-drag-drop-001",
)


def prepare(case_id: str, probe, *, app_feed: bool = False):
    if case_id == "uia-notepad-fill-001":
        return _notepad(probe)
    if case_id == "uia-calculator-digit-001":
        return _calculator(probe)
    if case_id.startswith("screen-blender-"):
        return _blender(probe, case_id, app_feed)
    if case_id.startswith("screen-unity-"):
        return _unity(probe, case_id)
    if case_id == "screen-vscode-fill-save-001":
        return _vscode(probe)
    if case_id == "screen-tk-scroll-select-001":
        return _tk_scroll(probe)
    if case_id == "screen-tk-drag-drop-001":
        return _tk_drag(probe)
    raise ValueError(f"no Phase I setup for {case_id}")
