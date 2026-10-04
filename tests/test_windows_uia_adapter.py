from finitact.action_adapter import Freshness
from finitact.runs import Goal, WindowsRunRequest
from finitact.windows_uia_adapter import (
    WindowsTarget,
    WindowsUIAAdapter,
    _decode_powershell_output,
    windows_uia_adapter_factory,
)


class Bridge:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.scripts = []

    def run(self, script):
        self.scripts.append(script)
        return next(self.responses)


def test_powershell_bridge_output_accepts_utf8_and_cp932_error_text():
    assert _decode_powershell_output("成功".encode()) == "成功"
    assert _decode_powershell_output("エラー".encode("cp932")) == "エラー"


def snapshot(*, runtime="42.1", name="Editor", value="draft", value_read_only=False, native_window_handle=200):
    return {
        "scope_ok": True,
        "complete": True,
        "errors": [],
        "elements": [
            {
                "runtime_id": runtime,
                "scope_hwnd": 100,
                "control_type": "ControlType.Document",
                "native_window_handle": native_window_handle,
                "automation_id": "Text Area",
                "name": name,
                "is_enabled": True,
                "is_offscreen": False,
                "patterns": ["ValuePatternIdentifiers.Pattern"],
                "value": value,
                "value_read_only": value_read_only,
                "selection_group": None,
                "selection_is_selected": None,
                "rect": [1, 2, 300, 200],
            }
        ],
    }


def test_observe_maps_live_uia_shape_to_action_adapter_contract():
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(snapshot()))
    observation = adapter.observe()
    assert observation.complete
    assert [candidate.operation for candidate in observation.candidates] == ["fill", "open_context_menu"]
    assert all(observation.untrusted_values[candidate.id] == "draft" for candidate in observation.candidates)
    assert "value" not in observation.candidates[0].attributes
    assert observation.candidates[0].attributes["scope_hwnd"] == 100
    assert observation.candidates[1].attributes["native_window_handle"] == 200


def test_fresh_reports_rebound_for_one_semantically_identical_runtime_replacement():
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(snapshot(), snapshot(runtime="42.2")))
    original = adapter.observe()
    assert adapter.fresh(original, original.candidates[0]) is Freshness.REBOUND


def test_read_only_value_pattern_is_context_not_a_fill_candidate():
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(snapshot(value_read_only=True)))
    observation = adapter.observe()
    assert observation.complete
    assert [candidate.operation for candidate in observation.candidates] == ["open_context_menu"]


def test_fill_uses_pattern_and_base64_encodes_untrusted_text():
    bridge = Bridge(snapshot(), {"scope_ok": True, "status": "confirmed", "detail": None})
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=bridge)
    observation = adapter.observe()
    result = adapter.act(observation.candidates[0], observation, text="a'; Remove-Item C:\\*")
    assert result.status == "confirmed"
    assert result.input_method == "pattern"
    assert "Remove-Item" not in bridge.scripts[-1]
    assert "VisibleOwnedWindows" in bridge.scripts[-1]
    assert "$wantedScope = [Int64]100" in bridge.scripts[-1]


def test_context_menu_message_is_confirmed_only_after_popup_observation():
    bridge = Bridge(snapshot(), {"scope_ok": True, "status": "confirmed", "detail": None})
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=bridge)
    observation = adapter.observe()
    candidate = next(item for item in observation.candidates if item.operation == "open_context_menu")
    result = adapter.act(candidate, observation)
    assert result.status == "confirmed"
    assert result.input_method == "window_message"
    assert "$native -ne [Int64]200" in bridge.scripts[-1]
    assert "SendContextMenu($native" in bridge.scripts[-1]
    assert "[System.Windows.Automation.ControlType]::MenuItem" in bridge.scripts[-1]
    assert "no new owned popup was observed" in bridge.scripts[-1]


def test_context_menu_accepted_without_observed_popup_is_uncertain_and_not_retried():
    bridge = Bridge(
        snapshot(),
        {"scope_ok": True, "status": "uncertain", "detail": "message accepted but no popup"},
    )
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=bridge)
    observation = adapter.observe()
    candidate = next(item for item in observation.candidates if item.operation == "open_context_menu")
    result = adapter.act(candidate, observation)
    assert result.status == "uncertain"
    assert result.input_method == "window_message"
    assert len(bridge.scripts) == 2


def test_context_menu_message_without_native_hwnd_is_not_offered():
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(snapshot(native_window_handle=0)))
    assert [candidate.operation for candidate in adapter.observe().candidates] == ["fill"]


def request(target_id="uia:100:7", **overrides):
    values = {
        "run_id": "live-1",
        "target_id": target_id,
        "goals": (Goal("g1", "Fill the document"),),
        "allowed_operations": ("fill",),
    }
    values.update(overrides)
    return WindowsRunRequest(**values)


def test_factory_resolves_explicit_hwnd_and_pid():
    adapter = windows_uia_adapter_factory(request())
    assert adapter.target == WindowsTarget(100, 7)


def test_factory_rejects_ambiguous_targets():
    for wanted in (request("notepad"), request("uia:0:7"), request("uia:100:-7")):
        try:
            windows_uia_adapter_factory(wanted)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe live target was accepted")



def test_factory_remains_pattern_only_when_run_allows_synthetic_input():
    adapter = windows_uia_adapter_factory(
        request(synthetic_input_allowed=True, exclusive_environment_ref="audit:galleria")
    )
    assert isinstance(adapter, WindowsUIAAdapter)


def test_powershell_bridge_sends_a_script_over_the_command_line_limit_as_a_file(monkeypatch):
    """The fill script with the pointer header exceeded 32767 chars encoded (E2E-01 WinError 206)."""
    import subprocess

    from finitact import windows_uia_adapter

    seen = []

    def fake_run(argv, **kwargs):
        path = argv[argv.index("-File") + 1] if "-File" in argv else None
        seen.append((argv, open(path, encoding="utf-8-sig").read() if path else None))
        return subprocess.CompletedProcess(argv, 0, b'{"ok": true}', b"")

    monkeypatch.setattr(windows_uia_adapter.subprocess, "run", fake_run)
    bridge = windows_uia_adapter.PowerShellBridge()
    assert bridge.run("x" * 20_000) == {"ok": True}
    assert bridge.run("y" * 100) == {"ok": True}
    assert seen[0][1] == "x" * 20_000 and "-EncodedCommand" not in seen[0][0]
    assert "-EncodedCommand" in seen[1][0]


def edit_snapshot(*, has_focus=False):
    raw = snapshot(native_window_handle=0)
    raw["elements"][0].update(
        control_type="ControlType.Edit", name="Email", value="", is_keyboard_focusable=True, has_keyboard_focus=has_focus
    )
    return raw


def test_a_text_input_offers_focus_beside_fill_until_it_has_focus():
    # E2E-I36: "click the Email field" had only fill to choose, and fill had nothing to type.
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(edit_snapshot(), edit_snapshot(has_focus=True)))
    assert [candidate.operation for candidate in adapter.observe().candidates] == ["fill", "focus"]
    assert [candidate.operation for candidate in adapter.observe().candidates] == ["fill"]


def test_focus_sets_uia_focus_and_requires_it_to_be_taken():
    bridge = Bridge(edit_snapshot(), {"scope_ok": True, "status": "confirmed", "detail": None})
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=bridge)
    observation = adapter.observe()
    focus = next(candidate for candidate in observation.candidates if candidate.operation == "focus")
    assert adapter.act(focus, observation).status == "confirmed"
    assert "$el.SetFocus()" in bridge.scripts[-1] and "HasKeyboardFocus" in bridge.scripts[-1]


def slider_snapshot():
    raw = snapshot(native_window_handle=0)
    raw["elements"][0].update(
        control_type="ControlType.Slider", name="Volume", value=None, patterns=["RangeValuePatternIdentifiers.Pattern"]
    )
    return raw


def test_set_range_sets_a_slider_to_a_number_and_refuses_other_text():
    # BUG-0050: the Settings volume slider had no executable operation for the caller's value.
    bridge = Bridge(slider_snapshot(), {"scope_ok": True, "status": "confirmed", "detail": None})
    adapter = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=bridge)
    observation = adapter.observe()
    slider = next(candidate for candidate in observation.candidates if candidate.operation == "set_range")
    assert adapter.act(slider, observation, text="thirty").status == "not_attempted"
    assert adapter.act(slider, observation).status == "not_attempted"
    assert adapter.act(slider, observation, text="30").status == "confirmed"
    assert "RangeValuePattern" in bridge.scripts[-1]


def test_nested_twins_with_the_same_name_and_rect_become_one_candidate():
    raw = snapshot()
    twin = dict(raw["elements"][0], runtime_id="42.9")
    raw["elements"].append(twin)
    observation = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(raw)).observe()
    assert [candidate.subject for candidate in observation.candidates] == ["42.1", "42.1"]


def test_a_later_run_adopts_a_retained_observation_for_a_pick():
    # BUG-0037: uncertain uia runs offered refs that every pick then rejected as unretained.
    first = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(snapshot()))
    observation = first.observe()
    later = WindowsUIAAdapter(WindowsTarget(100, 7), bridge=Bridge(snapshot()))
    later.adopt(observation, first.retain(observation))
    assert later.fresh(observation, observation.candidates[0]) is Freshness.FRESH
