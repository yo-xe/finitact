import threading

import pytest

from finitact.evaluation_runner import FAILURE, SUCCESS, UNDETERMINED, TargetMismatch, evaluate_run
from finitact.windows_evaluation_oracles import (
    PopupOpensOracle,
    PopupSelectOracle,
    ValueOracle,
    WindowTarget,
    automation_id_number,
    class_is,
    control_text,
    menu_items_visible,
    ocr_value_right_of,
    same_pid_windows,
    saved_text,
    sole_child,
    target_state,
)

TARGET = WindowTarget(hwnd=100, process_id=7)
POPUP = {"hwnd": 200, "class_name": "UnityPopupWndClass", "title": ""}
UNITY_POPUP = same_pid_windows(class_is("UnityPopupWndClass"))


def fake_value(probe, target):
    return probe.value


class FakeProbe:
    """Target state the driver mutates; the poller reads it concurrently like a real window."""

    def __init__(self, *, value="FREE ASPECT", text=""):
        self.lock = threading.Lock()
        self.windows = [{"hwnd": 100, "class_name": "UnityContainerWndClass", "title": "Unity"}]
        self.value = value
        self.text = text
        self.alive = True

    def owner_pid(self, hwnd):
        return 7 if self.alive else None

    def visible_windows(self, process_id):
        with self.lock:
            return list(self.windows)

    def window_text(self, hwnd):
        return self.text

    def set(self, **changes):
        with self.lock:
            for key, value in changes.items():
                setattr(self, key, value)


def settle(probe, **changes):
    """Apply a change and hold it long enough for the 5 ms poller to see it."""
    probe.set(**changes)
    threading.Event().wait(0.05)


def run(oracle, drive):
    return evaluate_run(
        case_id="case", run_id="run-1", target={"hwnd": 100, "pid": 7}, oracle=oracle, drive=drive, poll_interval_s=0.005
    )


def test_two_action_success_needs_the_popup_to_be_seen_open_then_closed_with_the_expected_value():
    probe = FakeProbe()
    oracle = PopupSelectOracle(TARGET, UNITY_POPUP, fake_value, "FULL HD", probe=probe)

    def drive():
        settle(probe, windows=[*probe.windows, POPUP])
        settle(probe, windows=probe.windows[:1], value="FULL HD")
        return {"goals": [{"outcome": "unverified"}]}

    record = run(oracle, drive)
    assert record["external"]["verdict"] == SUCCESS
    # The system's own answer is kept verbatim beside the external verdict (ADR-0014 decision 2).
    assert record["system_result"] == {"goals": [{"outcome": "unverified"}]}
    assert [bool(t["state"]["popups"]) for t in record["transitions"]] == [False, True, False]
    assert record["initial_state"] == {"holds": True, "detail": None}


def test_value_reached_without_an_observed_popup_is_undetermined_not_inferred():
    probe = FakeProbe()
    oracle = PopupSelectOracle(TARGET, UNITY_POPUP, fake_value, "FULL HD", probe=probe)
    record = run(oracle, lambda: settle(probe, value="FULL HD"))
    assert record["external"]["verdict"] == UNDETERMINED


def test_initial_state_violation_is_recorded_and_the_system_is_not_driven():
    probe = FakeProbe(value="FULL HD")
    oracle = PopupSelectOracle(TARGET, UNITY_POPUP, fake_value, "FULL HD", probe=probe)
    driven = []
    record = run(oracle, lambda: driven.append(True))
    assert driven == []
    assert record["initial_state"]["holds"] is False
    assert record["external"] == {"verdict": UNDETERMINED, "detail": "initial_state not met: value already reads 'FULL HD'", "driven": False}


def test_target_lost_during_run_is_undetermined():
    probe = FakeProbe()
    oracle = PopupOpensOracle(TARGET, UNITY_POPUP, probe=probe)
    record = run(oracle, lambda: settle(probe, alive=False))
    assert record["external"]["verdict"] == UNDETERMINED
    assert "target mismatch" in record["external"]["detail"]
    assert any(error["error"] == TargetMismatch.__name__ for error in record["polling"]["errors"])


def test_single_action_popup_that_never_appears_is_a_failure_even_when_the_driver_raises():
    probe = FakeProbe()
    oracle = PopupOpensOracle(TARGET, UNITY_POPUP, probe=probe)

    def drive():
        raise RuntimeError("outer agent crashed")

    record = run(oracle, drive)
    assert record["external"]["verdict"] == FAILURE
    assert record["drive_error"] == {"error": "RuntimeError", "detail": "outer agent crashed"}


@pytest.mark.parametrize(("final", "verdict"), [("FINITACT-UIA-001", SUCCESS), ("FINITACT-UIA-00", FAILURE)])
def test_control_text_compares_the_exact_native_text(final, verdict):
    probe = FakeProbe()
    oracle = ValueOracle(TARGET, control_text(101), "FINITACT-UIA-001", initial="", probe=probe)
    record = run(oracle, lambda: probe.set(text=final))
    assert record["external"]["verdict"] == verdict


def test_value_oracle_refuses_a_baseline_other_than_the_registered_initial_value():
    probe = FakeProbe(value="12")
    oracle = ValueOracle(TARGET, fake_value, "7", initial="0", probe=probe)
    record = run(oracle, lambda: None)
    assert record["external"]["driven"] is False
    assert "not the registered '0'" in record["external"]["detail"]


class Region:
    def __init__(self, label, rect):
        self.label, self.rect = label, rect


class FakeExtractor:
    def __init__(self, regions):
        self.regions = regions

    def extract(self, frame):
        return self.regions


class CaptureProbe(FakeProbe):
    def capture(self, hwnd):
        return None


def test_menu_drawn_inside_the_window_counts_as_open_only_with_enough_distinct_items():
    items = ("Edit Mode", "Sculpt Mode", "Vertex Paint", "Weight Paint")
    closed = FakeExtractor([Region("Object Mode", (50, 50, 90, 16)), Region("Texture Paint", (500, 30, 80, 14))])
    opened = FakeExtractor([Region(label, (60, 80 + 22 * i, 90, 16)) for i, label in enumerate(items)])
    assert menu_items_visible(items, closed, min_visible=3)(CaptureProbe(), TARGET) == []
    assert menu_items_visible(items, opened, min_visible=3)(CaptureProbe(), TARGET) == [{"menu_items": sorted(items)}]


class NameProbe(FakeProbe):
    def __init__(self, name):
        super().__init__()
        self.name = name

    def uia_name(self, hwnd, automation_id):
        return self.name


@pytest.mark.parametrize(
    ("name", "value"), [("表示は 7 です", "7"), ("Display is -1,234.5", "-1234.5"), ("表示は 0 です", "0")]
)
def test_automation_id_number_reads_the_display_number(name, value):
    assert automation_id_number("CalculatorResults")(NameProbe(name), TARGET) == value


def test_automation_id_number_without_one_number_is_no_reading():
    with pytest.raises(RuntimeError, match="one number"):
        automation_id_number("CalculatorResults")(NameProbe("表示は です"), TARGET)


def test_value_right_of_anchor_takes_the_nearest_same_row_line():
    extractor = FakeExtractor([
        Region("Game", (10, 10, 40, 14)),
        Region("Display 1", (10, 40, 60, 14)),
        Region("Scale", (400, 40, 40, 14)),
        Region("Full HD (1920×1080)", (90, 42, 140, 14)),
        Region("Free Aspect", (90, 80, 90, 14)),
    ])
    assert ocr_value_right_of("Display 1", extractor)(CaptureProbe(), TARGET) == "fullhd(1920x1080)"


def test_target_state_is_bound_to_the_target_pid_and_must_be_fresh(tmp_path):
    import json
    import os

    path = tmp_path / "state.json"
    path.write_text(json.dumps({"pid": 7, "workspaces": ["Shading"], "active_object_mode": "OBJECT"}))
    workspace = target_state(str(path), lambda state: state["workspaces"][0])
    assert workspace(FakeProbe(), TARGET) == "Shading"
    with pytest.raises(TargetMismatch):
        workspace(FakeProbe(), WindowTarget(hwnd=100, process_id=8))
    os.utime(path, (0, 0))
    with pytest.raises(RuntimeError, match="not running"):
        workspace(FakeProbe(), TARGET)


def test_target_state_retries_while_the_writer_is_replacing_json(tmp_path):
    import json
    import time

    path = tmp_path / "state.json"
    path.write_text("")

    def finish_write():
        time.sleep(0.015)
        path.write_text(json.dumps({"pid": 7, "selected": "TARGET ROW"}))

    writer = threading.Thread(target=finish_write)
    writer.start()
    try:
        selected = target_state(str(path), lambda state: state["selected"])
        assert selected(FakeProbe(), TARGET) == "TARGET ROW"
    finally:
        writer.join()


def test_sole_child_refuses_to_guess_between_several_controls():
    class ChildProbe(FakeProbe):
        def __init__(self, classes):
            super().__init__()
            self.classes = classes

        def children(self, hwnd):
            return [{"hwnd": 300 + i, "class_name": name, "process_id": 7} for i, name in enumerate(self.classes)]

    assert sole_child(ChildProbe(["Edit", "RichEditD2DPT"]), 100, "RichEditD2DPT") == 301
    with pytest.raises(TargetMismatch):
        sole_child(ChildProbe(["RichEditD2DPT", "RichEditD2DPT"]), 100, "RichEditD2DPT")


def test_settle_keeps_polling_so_a_change_that_lands_after_the_driver_returns_is_judged():
    probe = FakeProbe(value="Layout")
    oracle = ValueOracle(TARGET, fake_value, "Shading", probe=probe)
    threading.Timer(0.05, probe.set, kwargs={"value": "Shading"}).start()
    record = evaluate_run(
        case_id="case", run_id="run-1", target={}, oracle=oracle, drive=lambda: None, poll_interval_s=0.005, settle_s=0.3
    )
    assert record["external"]["verdict"] == SUCCESS


def test_saved_text_normalizes_crlf_and_the_final_newline_only(tmp_path):
    path = tmp_path / "doc.py"
    path.write_bytes('value = "日本語"\r\n\r\n'.encode("utf-8"))
    assert saved_text(str(path))(FakeProbe(), TARGET) == 'value = "日本語"'
    path.write_bytes(b"  x \n")
    assert saved_text(str(path))(FakeProbe(), TARGET) == "  x "
