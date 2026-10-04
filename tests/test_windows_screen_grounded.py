import base64
import gzip

import pytest

from finitact.runs import Goal, WindowsRunRequest
from finitact.screen_grounded_adapter import KEY_ALLOWLIST, ScreenGroundedAdapter, WindowFrame
from finitact.stage1_extractors import EdgeRectangleRegionExtractor
from finitact.windows_interaction_lease import WindowsInputScope
from finitact.windows_screen_grounded import (
    FillFocusChanged,
    FillTargetRefused,
    OwnedWindow,
    WindowsOwnedWindowEnumerator,
    WindowsPrintWindowCapture,
    WindowsInProcessPointer,
    WindowsSendInputPointer,
    WindowsWindowMessagePointer,
    key_events,
    minimum_idle_seconds,
    text_events,
    windows_screen_grounded_adapter_factory,
)
from finitact.windows_uia_adapter import WindowsTarget


class Bridge:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.scripts = []
        self.stdins = []

    def run(self, script, stdin=None):
        self.scripts.append(script)
        self.stdins.append(stdin)
        return next(self.responses)


def test_print_window_capture_returns_tightly_packed_bgra_frame():
    pixels = bytes(range(32))
    bridge = Bridge(
        {
            "scope_ok": True,
            "captured": True,
            "hwnd": 100,
            "process_id": 7,
            "width": 2,
            "height": 4,
            "pixels_gzip_base64": base64.b64encode(gzip.compress(pixels)).decode(),
        }
    )
    result = WindowsPrintWindowCapture(WindowsTarget(100, 7), bridge=bridge).capture()
    assert result == WindowFrame(100, 7, 2, 4, pixels)
    assert "PrintWindow($hwnd, $dc, 2)" in bridge.scripts[0]
    assert "ProcessId -eq 7" in bridge.scripts[0]


def test_capture_rejects_scope_mismatch_without_decoding_pixels():
    bridge = Bridge({"scope_ok": False, "captured": False, "detail": "scope mismatch"})
    try:
        WindowsPrintWindowCapture(WindowsTarget(100, 7), bridge=bridge).capture()
    except RuntimeError as exc:
        assert "configured HWND" in str(exc)
    else:
        raise AssertionError("scope mismatch was accepted")


def test_pointer_verification_and_send_both_include_foreground_hit_test_and_pid():
    bridge = Bridge(
        {"valid": True, "sent": False, "events": 0},
        {"valid": True, "sent": True, "events": 2},
    )
    pointer = WindowsSendInputPointer(bridge=bridge)
    assert pointer.target_is_valid(hwnd=100, process_id=7, point=(20, 30))
    assert pointer.click(hwnd=100, process_id=7, point=(20, 30))
    assert all("GetForegroundWindow" in script and "WindowFromPoint" in script for script in bridge.scripts)
    assert all("$actualPid -eq 7" in script for script in bridge.scripts)
    assert "$screenPoint.X = $rect.Left + 20" in bridge.scripts[1]
    assert "[FinitactPointer]::Click" not in bridge.scripts[0].split("if ($valid -and $false)")[0]


def test_capture_and_pointer_scripts_share_per_monitor_dpi_before_any_window_rect():
    # BUG-0017: capture and pointer must read GetWindowRect in the same (physical) coordinate space.
    bridge = Bridge(
        {"scope_ok": False, "captured": False, "detail": "scope mismatch"},
        {"scope_ok": False, "detail": "scope mismatch"},
        {"valid": True, "sent": False, "events": 0},
        {"valid": True, "move_sent": True, "child_hwnd": 5},
    )
    target = WindowsTarget(hwnd=100, process_id=7)
    with pytest.raises(RuntimeError):
        WindowsPrintWindowCapture(target, bridge=bridge).capture()
    with pytest.raises(RuntimeError):
        WindowsPrintWindowCapture(target, bridge=bridge).capture_scope()
    WindowsSendInputPointer(bridge=bridge).target_is_valid(hwnd=100, process_id=7, point=(20, 30))
    WindowsWindowMessagePointer(bridge=bridge).hover(hwnd=100, process_id=7, point=(20, 30))
    assert len(bridge.scripts) == 4
    for script in bridge.scripts:
        dpi = script.index("SetProcessDpiAwarenessContext([IntPtr]-4)")
        assert "throw 'per-monitor DPI awareness unavailable'" in script
        assert dpi < script.index("GetWindowRect(")


def test_pointer_allows_only_the_listed_foreground_windows_for_an_owned_popup():
    bridge = Bridge({"valid": True, "sent": False, "events": 0}, {"valid": True, "sent": False, "events": 0})
    pointer = WindowsSendInputPointer(bridge=bridge)
    pointer.target_is_valid(hwnd=100, process_id=7, point=(1, 1))
    pointer.target_is_valid(hwnd=200, process_id=7, point=(1, 1), foreground_hwnds=(100, 200))
    assert "[int64[]]@(100)" in bridge.scripts[0]
    assert "[int64[]]@(100,200)" in bridge.scripts[1]
    # hit-test stays pinned to the candidate's own window, never to the allowed set (ADR-0012 A).
    assert "$allowedHit = [int64[]]@(100)" in bridge.scripts[0]
    assert "$allowedHit = [int64[]]@(200)" in bridge.scripts[1]


def _encoded(pixels):
    return base64.b64encode(gzip.compress(pixels)).decode()


def test_scope_capture_returns_root_first_then_owned_popups():
    bridge = Bridge(
        {
            "scope_ok": True,
            "windows": [
                {"hwnd": 100, "width": 1, "height": 1, "pixels_gzip_base64": _encoded(bytes(4))},
                {"hwnd": 200, "width": 2, "height": 1, "pixels_gzip_base64": _encoded(bytes(8))},
            ],
        }
    )
    capture = WindowsPrintWindowCapture(WindowsTarget(100, 7), bridge=bridge)
    frames = capture.capture_scope()
    assert [(frame.hwnd, frame.process_id, frame.width) for frame in frames] == [(100, 7, 1), (200, 7, 2)]
    assert "OwnedBy(h, root)" in bridge.scripts[0]
    assert "pid == expectedPid" in bridge.scripts[0]


def test_scope_capture_rejects_a_result_without_the_root_first():
    bridge = Bridge(
        {
            "scope_ok": True,
            "windows": [{"hwnd": 200, "width": 1, "height": 1, "pixels_gzip_base64": _encoded(bytes(4))}],
        }
    )
    with pytest.raises(RuntimeError, match="root window capture failed"):
        WindowsPrintWindowCapture(WindowsTarget(100, 7), bridge=bridge).capture_scope()


def test_pointer_does_not_report_partial_send_as_confirmed():
    pointer = WindowsSendInputPointer(bridge=Bridge({"valid": True, "sent": True, "events": 1}))
    assert not pointer.click(hwnd=100, process_id=7, point=(20, 30))


def test_drag_validates_both_endpoints_and_requires_complete_native_sequence():
    bridge = Bridge({"valid": True, "sent": True, "events": 2, "moves": 8})
    pointer = WindowsSendInputPointer(bridge=bridge)
    assert pointer.drag(hwnd=100, process_id=7, start=(10, 20), end=(70, 80))
    script = bridge.scripts[0]
    assert "WindowFromPoint($start)" in script and "WindowFromPoint($end)" in script
    assert "$actualPid -eq 7" in script
    assert "[FinitactPointer]::Drag($start.X,$start.Y,$end.X,$end.Y,8)" in script
    assert "finally" in script and "0x0004" in script

    incomplete = WindowsSendInputPointer(
        bridge=Bridge({"valid": True, "sent": True, "events": -1, "moves": 8})
    )
    assert not incomplete.drag(hwnd=100, process_id=7, start=(10, 20), end=(70, 80))


def test_window_message_pointer_separates_hover_from_one_click_sequence():
    bridge = Bridge(
        {"valid": True, "move_sent": True, "child_hwnd": 222},
        {"valid": True, "down_started": True, "sequence_complete": True, "child_hwnd": 222},
    )
    pointer = WindowsWindowMessagePointer(bridge=bridge)
    child = pointer.hover(hwnd=100, process_id=7, point=(20, 30))
    assert child == 222
    assert pointer.click_once(hwnd=100, process_id=7, point=(20, 30), expected_child_hwnd=child)
    assert "0x0200" in bridge.scripts[0]
    assert "0x0201" in bridge.scripts[1] and "0x0202" in bridge.scripts[1]
    assert "ScreenToClient" in bridge.scripts[1]
    assert "SMTO_ABORTIFHUNG" in bridge.scripts[1]


def test_window_message_pointer_marks_incomplete_down_up_sequence_uncertain():
    from finitact.action_adapter import MutationUncertain

    pointer = WindowsWindowMessagePointer(
        bridge=Bridge(
            {
                "valid": True,
                "down_started": True,
                "sequence_complete": False,
                "detail": "WM_LBUTTONUP timed out",
            }
        )
    )
    try:
        pointer.click_once(hwnd=100, process_id=7, point=(20, 30), expected_child_hwnd=222)
    except MutationUncertain:
        pass
    else:
        raise AssertionError("incomplete mouse message sequence was accepted")


def test_owned_window_enumerator_lists_popups_beyond_the_acted_on_hwnd():
    bridge = Bridge(
        {
            "valid": True,
            "windows": [
                {"hwnd": 9830796, "class_name": "UnityContainerWndClass", "title": "SampleLab"},
                {"hwnd": 118164726, "class_name": "UnityPopupWndClass", "title": ""},
            ],
        }
    )
    windows = WindowsOwnedWindowEnumerator(bridge=bridge).list_visible(35824)
    assert windows == (
        OwnedWindow(9830796, "UnityContainerWndClass", "SampleLab"),
        OwnedWindow(118164726, "UnityPopupWndClass", ""),
    )
    assert "35824" in bridge.scripts[0]


def test_owned_window_enumerator_raises_on_invalid_result():
    enumerator = WindowsOwnedWindowEnumerator(bridge=Bridge({"valid": False, "detail": "boom"}))
    try:
        enumerator.list_visible(1)
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("invalid enumeration result was accepted")


def _screen_request(target_id="screen:100:7", **overrides):
    values = {
        "run_id": "live-1",
        "target_id": target_id,
        "goals": (Goal("g1", "Select 1366x768"),),
        "allowed_operations": ("click",),
    }
    values.update(overrides)
    return WindowsRunRequest(**values)


def test_screen_grounded_factory_resolves_explicit_hwnd_and_pid():
    adapter = windows_screen_grounded_adapter_factory(_screen_request())
    assert isinstance(adapter, ScreenGroundedAdapter)
    assert adapter.capture.target == WindowsTarget(100, 7)


def test_screen_grounded_factory_rejects_ambiguous_targets_and_native_only_synthetic_input():
    for wanted in (_screen_request("notepad"), _screen_request("screen:0:7"), _screen_request("uia:100:7")):
        try:
            windows_screen_grounded_adapter_factory(wanted)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe or wrong-namespace target was accepted")

    try:
        windows_screen_grounded_adapter_factory(
            _screen_request(synthetic_input_allowed=True, exclusive_environment_ref="verified:test")
        )
    except ValueError as exc:
        assert "Windows-native Python" in str(exc)
    else:
        raise AssertionError("synthetic input was accepted without an integrated native lease")


def test_screen_grounded_factory_click_is_not_attempted_without_synthetic_input_policy():
    pixels = bytes(4 * 4 * 4)
    bridge = Bridge(
        {
            "scope_ok": True,
            "windows": [
                {
                    "hwnd": 100,
                    "width": 4,
                    "height": 4,
                    "pixels_gzip_base64": base64.b64encode(gzip.compress(pixels)).decode(),
                }
            ],
        }
    )
    adapter = windows_screen_grounded_adapter_factory(_screen_request())
    adapter.capture = WindowsPrintWindowCapture(adapter.capture.target, bridge=bridge)
    # The policy gate is under test; the default OCR needs the optional `screen` extra (ADR-0016).
    adapter.extractor = EdgeRectangleRegionExtractor()
    observation = adapter.observe()
    from finitact.contracts import ObservedCandidate

    candidate = ObservedCandidate(id="x", operation="click", label="l", subject="s", attributes={"rect": (0, 0, 1, 1)})
    result = adapter.act(candidate, observation)
    assert result.status == "not_attempted"
    assert "not allowed" in (result.detail or "")


class _FakeIndicator:
    def show(self):
        return None

    def close(self):
        return None


def test_screen_grounded_factory_wires_three_layer_guard_for_native_windows(monkeypatch):
    import finitact.windows_screen_grounded as module

    monkeypatch.setenv("FINITACT_MIN_IDLE_SECONDS", "30")

    monkeypatch.setattr(
        module,
        "current_windows_input_scope",
        lambda environment: WindowsInputScope(environment, 1, "WinSta0", "Default"),
    )
    monkeypatch.setattr(module, "WindowsNamedInteractionLease", lambda scope: ("mutex", scope))
    monkeypatch.setattr(module, "WindowsIdleTimePrecondition", lambda **kwargs: ("idle", kwargs))
    monkeypatch.setattr(module, "WindowsSyntheticInputLease", lambda **kwargs: ("three-layers", kwargs))
    fake_indicator = _FakeIndicator()
    monkeypatch.setattr(module, "AutomationIndicator", lambda: fake_indicator)

    adapter = windows_screen_grounded_adapter_factory(
        _screen_request(synthetic_input_allowed=True, exclusive_environment_ref="audit:galleria")
    )

    assert adapter.guard.policy.allowed
    assert adapter.guard.policy.environment.identity == "audit:galleria"
    assert adapter.guard.lease[0] == "three-layers"
    assert adapter.guard.lease[1]["idle_time"] == (
        "idle", {"minimum_idle_seconds": 30.0, "ledger": module._SHARED_OWN_INPUT_LEDGER}
    )
    assert adapter.guard.lease[1]["indicator"] is fake_indicator
    assert adapter.indicator is fake_indicator


def test_blocked_input_environment_is_rejected_by_a_later_separate_run(monkeypatch):
    """A per-run adapter must not forget a block: the next run_windows(screen:...) call builds a
    brand new adapter/guard from scratch, so the block has to live in something shared across
    factory calls, not on the discarded adapter (ADR-0009's "blocked stays blocked" contract)."""

    import finitact.windows_screen_grounded as module

    monkeypatch.setattr(
        module,
        "current_windows_input_scope",
        lambda environment: WindowsInputScope(environment, 1, "WinSta0", "Default"),
    )
    monkeypatch.setattr(module, "WindowsNamedInteractionLease", lambda scope: None)
    monkeypatch.setattr(module, "WindowsIdleTimePrecondition", lambda **kwargs: None)
    monkeypatch.setattr(module, "WindowsSyntheticInputLease", lambda **kwargs: None)
    monkeypatch.setattr(module, "AutomationIndicator", lambda: _FakeIndicator())

    request = _screen_request(synthetic_input_allowed=True, exclusive_environment_ref="blocked-across-runs:test")
    first_run_adapter = windows_screen_grounded_adapter_factory(request)
    environment = first_run_adapter.guard.policy.environment
    first_run_adapter.guard.environment_state.block(environment, "synthetic input outcome was not confirmed")

    second_run_adapter = windows_screen_grounded_adapter_factory(request)
    # The OCR engine is loaded once per process, not per run.
    assert second_run_adapter.extractor is first_run_adapter.extractor
    assert second_run_adapter.guard.environment_state.blocked_reason(environment) == (
        "synthetic input outcome was not confirmed"
    )


def test_key_events_cover_the_allowlist_and_release_modifiers_last():
    for key in KEY_ALLOWLIST:
        events = key_events(key)
        assert len(events) % 2 == 0 and all(flags & 0x2 for _, _, flags in events[len(events) // 2 :])
    assert key_events("Shift+Tab") == [(0x10, 0, 0), (0x09, 0, 0), (0x09, 0, 2), (0x10, 0, 2)]
    assert key_events("Up")[0] == (0x26, 0, 1)
    with pytest.raises(ValueError):
        key_events("Alt+F4")


def test_text_events_send_unicode_units_and_newline_or_tab_as_keys():
    assert text_events("a\n\t") == [(0, 97, 4), (0, 97, 6), (0x0D, 0, 0), (0x0D, 0, 2), (0x09, 0, 0), (0x09, 0, 2)]
    assert [scan for _, scan, flags in text_events("😀") if flags == 4] == [0xD83D, 0xDE00]
    with pytest.raises(ValueError):
        text_events("\x1b")


def test_type_text_clicks_then_selects_all_and_types_only_if_foreground_stays_in_scope():
    expected = len(key_events("Ctrl+A")) + len(text_events("ab"))
    bridge = Bridge({"valid": True, "sent": True, "events": 2, "key_events": expected})
    pointer = WindowsSendInputPointer(bridge=bridge, text_delivery="keys")
    assert pointer.type_text(hwnd=100, process_id=7, point=(20, 30), text="ab")
    script = bridge.scripts[0]
    click, typing = script.index("[FinitactPointer]::Click"), script.index("[FinitactPointer]::Keys")
    assert click < script.index("Start-Sleep") < typing
    assert "@(17,65,65,17,0,0,0,0)" in script
    partial = Bridge({"valid": True, "sent": True, "events": 2, "key_events": 0})
    assert not WindowsSendInputPointer(bridge=partial).type_text(hwnd=100, process_id=7, point=(1, 1), text="ab")


def test_fill_checks_the_msaa_caret_around_the_click_and_refuses_keys_when_it_stayed_outside():
    """BUG-0033: a click on a caption left focus on the previous field, which Ctrl+A and the paste replaced."""
    fill = Bridge({"valid": True, "sent": True, "events": 2, "key_events": 8})
    WindowsSendInputPointer(bridge=fill).type_text(hwnd=100, process_id=7, point=(20, 30), text="a", region=(10, 20, 40, 16))
    script = fill.scripts[0]
    assert "-ReferencedAssemblies Accessibility" in script
    before, after = script.index("$caretBefore = [FinitactPointer]::Caret"), script.index("$caretAfter = [FinitactPointer]::Caret")
    assert before < script.index("[FinitactPointer]::Click") < after < script.index("[FinitactPointer]::Keys")
    assert "-not $caretRefused" in script and "-ge -22" in script and "-le 54" in script and "-ge 16" in script
    refused = Bridge({"valid": True, "sent": True, "events": 2, "key_events": 0, "caret_refused": True})
    with pytest.raises(FillTargetRefused):
        WindowsSendInputPointer(bridge=refused).type_text(hwnd=100, process_id=7, point=(1, 1), text="a", region=(0, 0, 4, 4))
    assert "$caretEntered = ($null -ne $caretAfter) -and -not $caretRefused" in script
    assert not WindowsSendInputPointer(bridge=fill).last_input_focus
    entered = WindowsSendInputPointer(bridge=Bridge({"valid": True, "sent": True, "events": 2, "key_events": 8, "caret_entered": True}))
    entered.type_text(hwnd=100, process_id=7, point=(1, 1), text="a", region=(0, 0, 4, 4))
    assert entered.last_input_focus
    unguarded = Bridge({"valid": True, "sent": True, "events": 2, "key_events": 8})
    WindowsSendInputPointer(bridge=unguarded).type_text(hwnd=100, process_id=7, point=(1, 1), text="a")
    assert "$caretBefore = [FinitactPointer]::Caret" not in unguarded.scripts[0]


def test_fill_returns_the_pointer_but_click_leaves_it_on_the_target():
    """BUG-0033: a pointer left on the last fill target kept its hover tooltip up for the next fill."""
    fill = Bridge({"valid": True, "sent": True, "events": 2, "key_events": 8})
    WindowsSendInputPointer(bridge=fill).type_text(hwnd=100, process_id=7, point=(20, 30), text="a")
    script = fill.scripts[0]
    assert script.index("GetCursorPos") < script.index("[FinitactPointer]::Click") < script.index(
        "[FinitactPointer]::Keys"
    ) < script.index("SetCursorPos($cursorBefore")
    click = Bridge({"valid": True, "sent": True, "events": 2})
    WindowsSendInputPointer(bridge=click).click(hwnd=100, process_id=7, point=(20, 30))
    assert "$cursorBefore" not in click.scripts[0]


def test_paste_delivery_passes_the_text_on_stdin_and_sets_the_clipboard_before_ctrl_v():
    bridge = Bridge({"valid": True, "sent": True, "events": 2, "key_events": 8})
    assert WindowsSendInputPointer(bridge=bridge).type_text(hwnd=100, process_id=7, point=(20, 30), text="a\n日" * 5000)
    script = bridge.scripts[0]
    assert script.index("Start-Sleep") < script.index("Set-Clipboard") < script.index("[FinitactPointer]::Keys")
    assert "@(17,65,65,17,17,86,86,17)" in script and "日" not in script
    assert base64.b64decode(bridge.stdins[0]).decode("utf-8") == "a\r\n日" * 5000


def test_key_press_checks_pid_and_foreground_scope_before_sending():
    bridge = Bridge({"valid": True, "key_events": 0}, {"valid": True, "key_events": 2})
    pointer = WindowsSendInputPointer(bridge=bridge)
    assert pointer.focus_is_valid(hwnd=100, process_id=7, foreground_hwnds=(100, 200))
    assert pointer.press_key(hwnd=100, process_id=7, key="Enter", foreground_hwnds=(100, 200))
    assert "::Keys" not in bridge.scripts[0]
    assert all("$actualPid -eq 7" in s and "@(100,200)" in s for s in bridge.scripts)
    assert not WindowsSendInputPointer(bridge=Bridge({"valid": True, "key_events": 1})).press_key(
        hwnd=100, process_id=7, key="Enter", foreground_hwnds=(100,)
    )


def test_screen_grounded_factory_offers_only_the_allowed_screen_operations():
    adapter = windows_screen_grounded_adapter_factory(_screen_request(allowed_operations=("toggle", "key", "click")))
    assert adapter.operations == ("click", "key")


def test_scroll_wheels_at_the_hit_tested_point_one_event_per_notch():
    bridge = Bridge({"valid": True, "sent": True, "events": 3}, {"valid": True, "sent": True, "events": 2})
    pointer = WindowsSendInputPointer(bridge=bridge)
    assert pointer.scroll(hwnd=100, process_id=7, point=(20, 30), notches=-3)
    assert "[FinitactPointer]::Wheel($screenPoint.X,$screenPoint.Y,-3)" in bridge.scripts[0]
    assert "::Click(" not in bridge.scripts[0].split("public static uint Keys")[1]
    assert "WindowFromPoint" in bridge.scripts[0] and "$actualPid -eq 7" in bridge.scripts[0]
    # A partial wheel is not a confirmed delivery.
    assert not pointer.scroll(hwnd=100, process_id=7, point=(20, 30), notches=3)
    with pytest.raises(ValueError):
        pointer.scroll(hwnd=100, process_id=7, point=(20, 30), notches=0)


def test_minimum_idle_defaults_low_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv("FINITACT_MIN_IDLE_SECONDS", raising=False)
    assert minimum_idle_seconds() == 1.0
    monkeypatch.setenv("FINITACT_MIN_IDLE_SECONDS", "30")
    assert minimum_idle_seconds() == 30.0


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "nan", "inf"])
def test_minimum_idle_refuses_a_non_positive_or_unparsable_setting(monkeypatch, raw):
    monkeypatch.setenv("FINITACT_MIN_IDLE_SECONDS", raw)
    with pytest.raises(ValueError, match="FINITACT_MIN_IDLE_SECONDS"):
        minimum_idle_seconds()


def test_delivery_scripts_bring_the_target_forward_before_checking_foreground_and_hit_test():
    """ADR-0033: the target is known, so a covered or background window no longer blocks delivery."""
    click = Bridge({"valid": True, "sent": True, "events": 2})
    WindowsSendInputPointer(bridge=click).click(hwnd=100, process_id=7, point=(1, 1))
    script = click.scripts[0]
    assert script.index("[FinitactPointer]::Front($target") < script.index("WindowFromPoint($screenPoint)")
    assert script.index("[FinitactPointer]::Front($target") < script.index("$foreground = [FinitactPointer]::GetForegroundWindow()")
    key = Bridge({"valid": True, "key_events": 2})
    WindowsSendInputPointer(bridge=key).press_key(hwnd=100, process_id=7, key="F2", foreground_hwnds=(100,))
    assert key.scripts[0].index("::Front($target") < key.scripts[0].index("$foreground = [FinitactPointer]::GetForegroundWindow()")


def test_pointer_gestures_are_confirmed_by_their_own_event_counts():
    double = Bridge({"valid": True, "sent": True, "events": 4})
    assert WindowsSendInputPointer(bridge=double).click(hwnd=100, process_id=7, point=(1, 1), gesture="double_click")
    assert "::Gesture($screenPoint.X,$screenPoint.Y,'double_click')" in double.scripts[0]
    short = Bridge({"valid": True, "sent": True, "events": 2})
    assert not WindowsSendInputPointer(bridge=short).click(hwnd=100, process_id=7, point=(1, 1), gesture="ctrl_click")
    with pytest.raises(ValueError):
        WindowsSendInputPointer(bridge=short).click(hwnd=100, process_id=7, point=(1, 1), gesture="triple_click")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({"pid_ok": True, "foreground": 5, "foreground_name": "5 PS 'x'", "hit_root": 100}, "foreground stayed on 5 PS 'x'"),
        ({"pid_ok": True, "foreground": 100, "hit_root": 9, "hit_root_name": "9 PS 'x'"}, "point is covered by 9 PS 'x'"),
        ({"pid_ok": False, "foreground": 100, "hit_root": 100}, "target window belongs to another process"),
    ],
)
def test_pointer_names_why_the_delivery_target_was_refused(raw, reason):
    pointer = WindowsSendInputPointer(bridge=Bridge({"valid": False, "sent": False, **raw}))
    assert not pointer.target_is_valid(hwnd=100, process_id=7, point=(1, 1))
    assert pointer.last_refusal == reason


class _Native:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def deliver(self, hwnd, process_id, point, **options):
        self.calls.append((hwnd, process_id, point, options))
        return next(self.responses)


def test_in_process_pointer_serves_verify_click_and_wheel_but_leaves_keys_to_powershell():
    """E2E-I20: same raw dict as the PowerShell script, so confirmation and refusal naming are unchanged."""
    native = _Native(
        {"valid": False, "sent": False, "pid_ok": True, "foreground": 100, "hit_root": 9, "hit_root_name": "9 X 'y'"},
        {"valid": True, "sent": True, "events": 4},
        {"valid": True, "sent": True, "events": 2},
    )
    bridge = Bridge({"valid": True, "key_events": 2})
    pointer = WindowsSendInputPointer(bridge=bridge, native=native)
    assert not pointer.target_is_valid(hwnd=100, process_id=7, point=(1, 1), foreground_hwnds=(100, 200))
    assert pointer.last_refusal == "point is covered by 9 X 'y'"
    assert pointer.click(hwnd=100, process_id=7, point=(1, 1), gesture="double_click")
    assert not pointer.scroll(hwnd=100, process_id=7, point=(1, 1), notches=-3)
    assert [call[3] for call in native.calls] == [
        {"send": False, "foreground_hwnds": (100, 200)},
        {"send": True, "foreground_hwnds": None, "gesture": "double_click"},
        {"send": True, "foreground_hwnds": None, "wheel": -3},
    ]
    with pytest.raises(ValueError):
        pointer.click(hwnd=100, process_id=7, point=(1, 1), gesture="triple_click")
    assert pointer.press_key(hwnd=100, process_id=7, key="F2", foreground_hwnds=(100,))
    assert len(bridge.scripts) == 1 and len(native.calls) == 3


def test_empty_fill_clears_with_delete_instead_of_an_empty_clipboard():
    bridge = Bridge({"valid": True, "sent": True, "events": 2, "key_events": 6})
    assert WindowsSendInputPointer(bridge=bridge).type_text(hwnd=100, process_id=7, point=(1, 1), text="")
    assert "Set-Clipboard" not in bridge.scripts[0] and bridge.stdins == [None]


def test_fill_reports_complete_click_and_no_key_attempt_after_focus_leaves_scope():
    bridge = Bridge({
        "valid": True, "sent": True, "events": 2, "key_events": 0,
        "focus_refused": True, "keys_attempted": False, "focus_after_name": "window:300:9 Search",
    })
    with pytest.raises(FillFocusChanged, match="no keys sent.*window:300:9"):
        WindowsSendInputPointer(bridge=bridge).type_text(hwnd=100, process_id=7, point=(1, 1), text="abc")
    script = bridge.scripts[0]
    assert script.index("$focusRefused = -not") < script.index("$keysAttempted = $true")
    assert script.index("$keysAttempted = $true") < script.index("[FinitactPointer]::Keys")


@pytest.mark.parametrize("changed", [
    {"events": 1}, {"key_events": 1}, {"keys_attempted": True}, {"keys_attempted": None},
    {"valid": False}, {"sent": False},
])
def test_fill_never_calls_partial_or_unknown_delivery_a_controlled_focus_refusal(changed):
    raw = {
        "valid": True, "sent": True, "events": 2, "key_events": 0,
        "focus_refused": True, "keys_attempted": False,
        **changed,
    }
    pointer = WindowsSendInputPointer(bridge=Bridge(raw))
    assert not pointer.type_text(hwnd=100, process_id=7, point=(1, 1), text="abc")


def test_shell_surface_peers_widen_foreground_and_hit_sets():
    """BUG-0048: open Start keeps the foreground on Search while its visible panel takes the pointer hit."""
    native = _Native(
        {"valid": True, "sent": False},
        {"valid": False, "sent": False, "pid_ok": True, "foreground": 300, "hit_root": 9, "hit_root_name": "9 X 'y'"},
    )
    bridge = Bridge({"valid": True, "key_events": 2}, {"valid": True, "sent": True, "events": 2, "key_events": 4})
    pointer = WindowsSendInputPointer(bridge=bridge, native=native, peers=lambda hwnd: (300,) if hwnd == 100 else ())
    assert pointer.target_is_valid(hwnd=100, process_id=7, point=(1, 1))
    assert not pointer.target_is_valid(hwnd=100, process_id=7, point=(1, 1))
    assert pointer.last_refusal == "point is covered by 9 X 'y'"
    assert native.calls[0][3] == {"send": False, "foreground_hwnds": (100, 300), "hit_hwnds": (100, 300)}
    assert pointer.press_key(hwnd=100, process_id=7, key="F2", foreground_hwnds=(100,))
    assert "$allowedForeground = [int64[]]@(100,300)" in bridge.scripts[0]
    pointer.type_text(hwnd=100, process_id=7, point=(1, 1), text="x")
    assert "$allowedHit = [int64[]]@(100,300)" in bridge.scripts[1]


def test_screen_grounded_factory_delivers_pointer_in_process():
    adapter = windows_screen_grounded_adapter_factory(_screen_request())
    assert isinstance(adapter.pointer.native, WindowsInProcessPointer)


def test_cross_window_drag_script_checks_each_endpoint_against_its_own_window():
    bridge = Bridge({"valid": True, "sent": True, "events": 2, "moves": 8})
    pointer = WindowsSendInputPointer(bridge=bridge)
    assert pointer.drag(hwnd=100, process_id=7, start=(10, 20), end=(70, 80), end_hwnd=200, end_process_id=9)
    script = bridge.scripts[0]
    assert "$endTarget = [IntPtr]200" in script
    assert "$endRect.Left + 70" in script and "$endRoot -eq $endTarget" in script
    assert "$endPid -eq 9" in script
    with pytest.raises(ValueError):
        pointer.drag(hwnd=100, process_id=7, start=(1, 1), end=(2, 2), end_hwnd=200)


def test_drop_target_needs_drag_and_differs_from_target():
    from finitact.runs import Goal, WindowsRunRequest

    base = dict(run_id="r", target_id="screen:1:2", goals=(Goal("g", "x"),))
    with pytest.raises(ValueError):
        WindowsRunRequest(**base, allowed_operations=("click",), drop_target_id="window:3:4")
    with pytest.raises(ValueError):
        WindowsRunRequest(**base, allowed_operations=("drag",), drop_target_id="screen:1:2")
    plain = WindowsRunRequest(**base, allowed_operations=("drag",))
    dropped = WindowsRunRequest(**base, allowed_operations=("drag",), drop_target_id="window:3:4")
    assert plain.fingerprint() != dropped.fingerprint()


def test_hidden_shell_holder_excuses_pointer_foreground_but_never_keys():
    """BUG-0057: dismissed Search keeps the foreground while cloaked or hidden; a click must not be refused for it."""
    native = _Native({"valid": True, "sent": True, "events": 2})
    bridge = Bridge({"valid": True, "key_events": 2}, {"valid": True, "sent": True, "events": 2, "key_events": 4})
    pointer = WindowsSendInputPointer(
        bridge=bridge, native=native, peers=lambda hwnd: (), hidden_peers=lambda hwnd: (500,) if hwnd == 100 else ()
    )
    assert pointer.click(hwnd=100, process_id=7, point=(1, 1))
    assert native.calls[0][3]["foreground_hwnds"] == (100, 500)
    assert native.calls[0][3]["hit_hwnds"] == (100,)
    assert pointer.press_key(hwnd=100, process_id=7, key="F2", foreground_hwnds=(100,))
    assert "$allowedForeground = [int64[]]@(100)" in bridge.scripts[0]
    pointer.type_text(hwnd=100, process_id=7, point=(1, 1), text="x")
    assert "500" not in bridge.scripts[1].split("$allowedForeground")[1].split("\n")[0]
