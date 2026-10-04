import json
from contextlib import contextmanager
from dataclasses import replace

import pytest

from finitact.action_adapter import Freshness, MutationUncertain, ScopeViolation
from finitact.interaction_lease import (
    ExclusiveInputEnvironment,
    InputEnvironmentState,
    LeaseGrant,
    SyntheticInputGuard,
    SyntheticInputPolicy,
)
from finitact.screen_grounded_adapter import KEY_ALLOWLIST, ScreenGroundedAdapter, VisualRegion, WindowFrame

ENVIRONMENT = ExclusiveInputEnvironment("isolated-desktop", "generation-1")


def frame(fill=0, *, hwnd=100, process_id=7):
    return WindowFrame(hwnd, process_id, 4, 3, bytes([fill, fill, fill, 255]) * 12)


def frame_with_unrelated_change():
    pixels = bytearray(frame().pixels)
    pixels[0:4] = bytes([1, 1, 1, 255])
    return WindowFrame(100, 7, 4, 3, bytes(pixels))


class Capture:
    def __init__(self, *frames):
        self.frames = iter(frames)

    def capture(self):
        return next(self.frames)


class Extractor:
    def extract(self, actual):
        return (VisualRegion("resolution", "Game view resolution", (1, 1, 2, 1), 0.99, "template:resolution"),)


class Lease:
    def __init__(self):
        self.acquired = 0

    @contextmanager
    def acquire(self, environment, *, deadline_monotonic):
        self.acquired += 1
        yield LeaseGrant(environment)


class Pointer:
    def __init__(self, *, valid=True, confirmed=True):
        self.valid = valid
        self.confirmed = confirmed
        self.checks = []
        self.clicks = []

    def target_is_valid(self, *, hwnd, process_id, point):
        self.checks.append((hwnd, process_id, point))
        return self.valid

    def click(self, *, hwnd, process_id, point):
        self.clicks.append((hwnd, process_id, point))
        return self.confirmed


def adapter(*frames, pointer=None, allowed=True):
    lease = Lease()
    guard = SyntheticInputGuard(
        policy=SyntheticInputPolicy(allowed, ENVIRONMENT),
        lease=lease,
        environment_state=InputEnvironmentState(),
        validate_environment=lambda actual: actual == ENVIRONMENT,
    )
    return (
        ScreenGroundedAdapter(
            capture=Capture(*frames),
            extractor=Extractor(),
            pointer=pointer or Pointer(),
            guard=guard,
            deadline_monotonic=10.0,
        ),
        lease,
    )


def test_observe_emits_finite_click_with_frame_and_anchor_evidence():
    subject, _ = adapter(frame())
    observation = subject.observe()
    assert observation.complete
    assert len(observation.candidates) == 1
    candidate = observation.candidates[0]
    assert candidate.operation == "click"
    assert candidate.label == "Game view resolution"
    assert candidate.attributes["source"] == "template"
    assert candidate.binding["evidence"] == "template:resolution"
    assert len(candidate.binding["anchor_fingerprint"]) == 64
    record = json.dumps(candidate.provider_record())
    assert "anchor_fingerprint" not in record and "template:resolution" not in record


def test_pointer_click_is_sent_once_under_lease_after_fresh_frame_and_hit_test():
    pointer = Pointer()
    original = frame()
    subject, lease = adapter(original, original, pointer=pointer)
    observation = subject.observe()
    result = subject.act(observation.candidates[0], observation)
    assert result.status == "confirmed"
    assert result.input_method == "synthetic_pointer"
    assert lease.acquired == 1
    assert pointer.checks == [(100, 7, (2, 1))]
    assert pointer.clicks == [(100, 7, (2, 1))]


def test_identical_frame_reuses_the_observed_extraction_but_changed_frame_reextracts():
    class CountingExtractor(Extractor):
        calls = 0

        def extract(self, actual):
            CountingExtractor.calls += 1
            return super().extract(actual)

    for second, expected_calls in ((frame(), 1), (frame_with_unrelated_change(), 2)):
        CountingExtractor.calls = 0
        subject, _ = adapter(frame(), second)
        subject.extractor = CountingExtractor()
        observation = subject.observe()
        assert subject.act(observation.candidates[0], observation).status == "confirmed"
        assert CountingExtractor.calls == expected_calls


def test_changed_frame_after_lease_is_stale_and_sends_nothing():
    pointer = Pointer()
    subject, lease = adapter(frame(), frame(fill=1), pointer=pointer)
    observation = subject.observe()
    result = subject.act(observation.candidates[0], observation)
    assert result.status == "not_attempted"
    assert "stale" in result.detail
    assert lease.acquired == 1
    assert pointer.checks == pointer.clicks == []


def test_unrelated_frame_change_is_allowed_when_candidate_and_anchor_are_reidentified():
    pointer = Pointer()
    subject, _ = adapter(frame(), frame_with_unrelated_change(), pointer=pointer)
    observation = subject.observe()
    result = subject.act(observation.candidates[0], observation)
    assert result.status == "confirmed"
    assert len(pointer.clicks) == 1


def test_hit_test_failure_sends_nothing():
    pointer = Pointer(valid=False)
    original = frame()
    subject, _ = adapter(original, original, pointer=pointer)
    observation = subject.observe()
    result = subject.act(observation.candidates[0], observation)
    assert result.status == "not_attempted"
    assert "delivery target verification failed" == result.detail
    assert pointer.clicks == []


def test_unconfirmed_pointer_delivery_is_uncertain_and_not_retried():
    pointer = Pointer(confirmed=False)
    original = frame()
    subject, lease = adapter(original, original, pointer=pointer)
    observation = subject.observe()
    with pytest.raises(MutationUncertain):
        subject.act(observation.candidates[0], observation)
    assert lease.acquired == 1
    assert len(pointer.clicks) == 1


def test_changed_window_identity_stops_as_scope_violation():
    subject, _ = adapter(frame(), frame(hwnd=101))
    observation = subject.observe()
    with pytest.raises(ScopeViolation):
        subject.act(observation.candidates[0], observation)


def test_fresh_never_rebinds_a_visual_candidate_to_a_changed_frame():
    original = frame()
    subject, _ = adapter(original, original, frame(fill=2))
    observation = subject.observe()
    assert subject.fresh(observation, observation.candidates[0]) is Freshness.FRESH
    assert subject.fresh(observation, observation.candidates[0]) is Freshness.STALE


def test_invalid_regions_are_rejected():
    with pytest.raises(ValueError):
        VisualRegion("resolution", "Resolution", (0, 0, 1, 1), 1.1, "template")


@pytest.mark.parametrize("operations", [("click",), ("fill",)])
def test_regions_past_the_frame_are_clipped_or_dropped(operations):
    class OverflowingExtractor:
        def extract(self, actual):
            return (
                VisualRegion("resolution", "Resolution", (3, 2, 2, 1), 0.5, "template"),
                VisualRegion("negative", "Negative", (-1, -1, 2, 2), 0.5, "template"),
                VisualRegion("outside", "Outside", (9, 9, 2, 2), 0.5, "template"),
            )

    subject, _ = adapter(frame(), frame())
    subject.operations = operations
    subject.extractor = OverflowingExtractor()
    observation = subject.observe()
    assert [(c.subject, c.attributes["rect"]) for c in observation.candidates] == [
        ("resolution", (3, 2, 1, 1)), ("negative", (0, 0, 1, 1))
    ]
    assert subject.fresh(observation, observation.candidates[0]) is Freshness.FRESH


class ScopeCapture:
    def __init__(self, *scopes):
        self.scopes = iter(scopes)

    def capture(self):
        raise AssertionError("scope-aware adapter must use capture_scope")

    def capture_scope(self):
        return next(self.scopes)


class PopupAwareExtractor:
    def extract(self, actual):
        if actual.hwnd == 200:
            return (VisualRegion("full-hd", "Full HD", (1, 1, 2, 1), 0.99, "ocr:Full HD"),)
        return (VisualRegion("resolution", "Game view resolution", (1, 1, 2, 1), 0.99, "template:resolution"),)


class ScopedPointer(Pointer):
    def __init__(self):
        super().__init__()
        self.scopes = []

    def target_is_valid(self, *, hwnd, process_id, point, **kwargs):
        self.scopes.append(kwargs)
        return super().target_is_valid(hwnd=hwnd, process_id=process_id, point=point)

    def click(self, *, hwnd, process_id, point, **kwargs):
        self.scopes.append(kwargs)
        return super().click(hwnd=hwnd, process_id=process_id, point=point)


class ClosableLease(Lease):
    def __init__(self):
        super().__init__()
        self.closed = 0

    def close(self):
        self.closed += 1


def scoped_adapter(*scopes, pointer):
    lease = ClosableLease()
    guard = SyntheticInputGuard(
        policy=SyntheticInputPolicy(True, ENVIRONMENT),
        lease=lease,
        environment_state=InputEnvironmentState(),
        validate_environment=lambda actual: actual == ENVIRONMENT,
    )
    subject = ScreenGroundedAdapter(
        capture=ScopeCapture(*scopes),
        extractor=PopupAwareExtractor(),
        pointer=pointer,
        guard=guard,
        deadline_monotonic=10.0,
    )
    return subject, lease


def test_owned_popup_candidates_are_bound_to_their_own_window():
    root, popup = frame(), frame(hwnd=200)
    subject, _ = scoped_adapter((root, popup), pointer=ScopedPointer())
    observation = subject.observe()
    by_label = {candidate.label: candidate for candidate in observation.candidates}
    assert "scope_hwnd" not in by_label["Game view resolution"].attributes
    assert by_label["Full HD"].attributes["scope_hwnd"] == 200
    assert by_label["Full HD"].attributes["scope"] == "owned_popup"
    assert observation.observation_id not in {root.fingerprint, popup.fingerprint}


def test_popup_click_targets_the_popup_and_allows_only_root_or_popup_foreground():
    root, popup = frame(), frame(hwnd=200)
    pointer = ScopedPointer()
    subject, _ = scoped_adapter((root, popup), (root, popup), pointer=pointer)
    observation = subject.observe()
    full_hd = next(candidate for candidate in observation.candidates if candidate.label == "Full HD")
    result = subject.act(full_hd, observation)
    assert result.status == "confirmed"
    assert pointer.clicks == [(200, 7, (2, 1))]
    assert pointer.scopes == [{"foreground_hwnds": (100, 200)}] * 2


def test_root_click_keeps_single_window_delivery_contract():
    root, popup = frame(), frame(hwnd=200)
    pointer = ScopedPointer()
    subject, _ = scoped_adapter((root, popup), (root, popup), pointer=pointer)
    observation = subject.observe()
    root_candidate = next(candidate for candidate in observation.candidates if "scope_hwnd" not in candidate.attributes)
    assert subject.act(root_candidate, observation).status == "confirmed"
    assert pointer.clicks == [(100, 7, (2, 1))]
    assert pointer.scopes == [{}, {}]


def test_vanished_popup_is_stale_and_sends_nothing():
    root, popup = frame(), frame(hwnd=200)
    pointer = ScopedPointer()
    subject, _ = scoped_adapter((root, popup), (root,), pointer=pointer)
    observation = subject.observe()
    full_hd = next(candidate for candidate in observation.candidates if candidate.label == "Full HD")
    result = subject.act(full_hd, observation)
    assert result.status == "not_attempted"
    assert "stale" in result.detail
    assert pointer.clicks == []


def test_screen_evidence_falls_back_to_root_when_the_popups_scope_closed():
    """BUG-0023: selecting a popup item can close the popup itself; the after-observation then has only the
    root frame, and achievement E needs that instead of losing all evidence to None."""
    root, popup = frame(), frame(hwnd=200)
    subject, _ = scoped_adapter((root, popup), (root,), pointer=ScopedPointer())
    before = subject.observe()
    full_hd = next(candidate for candidate in before.candidates if candidate.label == "Full HD")
    after = subject.observe()
    assert subject.screen_evidence(before, full_hd).frame is popup
    evidence = subject.screen_evidence(after, full_hd)
    assert evidence is not None and evidence.frame is root


def test_root_evidence_reads_the_root_frame_regardless_of_any_candidates_popup_scope():
    root, popup = frame(), frame(hwnd=200)
    subject, _ = scoped_adapter((root, popup), pointer=ScopedPointer())
    observation = subject.observe()
    assert subject.root_evidence(observation).frame is root


def test_popup_freshness_ignores_root_repaint_but_not_popup_change():
    root, popup = frame(), frame(hwnd=200)
    repainted_root = frame_with_unrelated_change()
    subject, _ = scoped_adapter(
        (root, popup), (repainted_root, popup), (root, frame(fill=9, hwnd=200)), (root,), pointer=ScopedPointer()
    )
    observation = subject.observe()
    full_hd = next(candidate for candidate in observation.candidates if candidate.label == "Full HD")
    assert subject.fresh(observation, full_hd) is Freshness.FRESH
    assert subject.fresh(observation, full_hd) is Freshness.STALE
    assert subject.fresh(observation, full_hd) is Freshness.STALE


def test_root_identity_change_is_a_scope_violation_even_for_popup_candidates():
    root, popup = frame(), frame(hwnd=200)
    subject, _ = scoped_adapter((root, popup), (frame(hwnd=101), popup), pointer=ScopedPointer())
    observation = subject.observe()
    full_hd = next(candidate for candidate in observation.candidates if candidate.label == "Full HD")
    with pytest.raises(ScopeViolation):
        subject.fresh(observation, full_hd)


def test_adapter_close_ends_the_run_scoped_lease():
    subject, lease = scoped_adapter((frame(),), pointer=ScopedPointer())
    subject.close()
    assert lease.closed == 1


def test_adopted_observation_from_another_adapter_is_rechecked_before_the_click():
    first, _ = adapter(frame())
    observation = first.observe()
    retained = first.retain(observation)
    pointer = Pointer()
    second, _ = adapter(frame(), frame(), pointer=pointer)

    second.adopt(observation, retained)

    assert second.fresh(observation, observation.candidates[0]) is Freshness.FRESH
    assert second.act(observation.candidates[0], observation).status == "confirmed"
    assert pointer.clicks == [(100, 7, (2, 1))]


def test_adopt_rejects_frames_that_did_not_produce_the_observation():
    first, _ = adapter(frame())
    observation = first.observe()
    second, _ = adapter()

    with pytest.raises(ScopeViolation):
        second.adopt(observation, replace(first.retain(observation), frames=(frame(fill=1),)))
    with pytest.raises(ScopeViolation):
        second.fresh(observation)


class InputPointer(Pointer):
    def __init__(self, *, valid=True, confirmed=True):
        super().__init__(valid=valid, confirmed=confirmed)
        self.typed = []
        self.regions = []
        self.focus_checks = []
        self.keys = []

    def type_text(self, *, hwnd, process_id, point, text, region=None):
        self.typed.append((hwnd, process_id, point, text))
        self.regions.append(region)
        return self.confirmed

    def focus_is_valid(self, *, hwnd, process_id, foreground_hwnds):
        self.focus_checks.append((hwnd, process_id, foreground_hwnds))
        return self.valid

    def press_key(self, *, hwnd, process_id, key, foreground_hwnds):
        self.keys.append((hwnd, process_id, key, foreground_hwnds))
        return self.confirmed


def input_adapter(*frames, pointer, operations=("click", "fill", "key")):
    subject, lease = adapter(*frames, pointer=pointer)
    subject.operations = operations
    return subject, lease


def test_pointer_gestures_are_offered_only_when_requested_and_sent_as_that_gesture():
    """ADR-0033: a desktop icon opens on double click; a second single click starts a rename instead."""

    class GesturePointer(Pointer):
        def click(self, *, hwnd, process_id, point, gesture="click"):
            self.clicks.append((hwnd, process_id, point, gesture))
            return self.confirmed

    pointer = GesturePointer()
    subject, _ = input_adapter(frame(), frame(), pointer=pointer, operations=("double_click", "right_click"))
    observation = subject.observe()
    assert [candidate.operation for candidate in observation.candidates] == ["double_click", "right_click"]
    assert len({candidate.id for candidate in observation.candidates}) == 2
    assert subject.act(observation.candidates[0], observation).status == "confirmed"
    assert pointer.clicks == [(100, 7, (2, 1), "double_click")]


def test_click_only_adapter_offers_neither_fill_nor_key():
    subject, _ = adapter(frame())
    assert [candidate.operation for candidate in subject.observe().candidates] == ["click"]


def test_fill_and_key_candidates_are_offered_only_when_enabled_and_click_ids_stay_stable():
    click_only = adapter(frame())[0].observe()
    subject, _ = input_adapter(frame(), pointer=InputPointer())
    observation = subject.observe()
    by_operation = {}
    for candidate in observation.candidates:
        by_operation.setdefault(candidate.operation, []).append(candidate)
    assert by_operation["click"][0].id == click_only.candidates[0].id
    fill = by_operation["fill"][0]
    assert fill.id != by_operation["click"][0].id
    assert (fill.label, fill.attributes["rect"]) == ("Game view resolution", (1, 1, 2, 1))
    assert [candidate.attributes["key"] for candidate in by_operation["key"]] == [
        key for key in KEY_ALLOWLIST if key != "Ctrl+A"
    ]
    key_only, _ = input_adapter(frame(), pointer=InputPointer(), operations=("key",))
    assert [candidate.attributes["key"] for candidate in key_only.observe().candidates] == list(KEY_ALLOWLIST)
    assert observation.semantic_id == click_only.semantic_id
    with pytest.raises(ValueError):
        ScreenGroundedAdapter(
            capture=Capture(), extractor=Extractor(), pointer=Pointer(), guard=None,
            deadline_monotonic=1.0, operations=("move",),
        )


class DragPointer(Pointer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.drags = []

    def drag(self, *, hwnd, process_id, start, end):
        self.drags.append((hwnd, process_id, start, end))
        return self.confirmed


def test_drag_start_is_non_mutating_and_end_is_one_checked_delivery():
    pointer = DragPointer()
    original = frame()
    subject, lease = adapter(original, original, pointer=pointer)
    subject.operations = ("drag",)
    observation = subject.observe()
    start = observation.candidates[0]
    assert start.attributes["drag_phase"] == "start"
    ends = subject.drag_end_observation(start, observation)
    assert len(ends.candidates) == 1
    end = ends.candidates[0]
    assert end.attributes["drag_phase"] == "end"
    assert "Game view resolution" in end.label
    assert lease.acquired == 0 and pointer.drags == []
    result = subject.act(end, ends)
    assert result.status == "confirmed"
    assert lease.acquired == 1
    assert pointer.checks == [(100, 7, (2, 1)), (100, 7, (2, 1))]
    assert pointer.drags == [(100, 7, (2, 1), (2, 1))]


def test_drag_end_includes_shape_candidate_but_start_does_not():
    class TextAndEdgeExtractor:
        def extract(self, actual):
            return (
                VisualRegion("text", "Slider", (0, 0, 2, 2), 0.9, "ocr:Slider"),
                VisualRegion("edge", "boxed_region_2x2", (2, 1, 2, 2), 0.5, "edge_contour:pixels=4"),
            )

    subject, _ = adapter(frame(), pointer=DragPointer())
    subject.operations = ("drag",)
    subject.extractor = TextAndEdgeExtractor()
    observation = subject.observe()
    assert [item.subject for item in observation.candidates] == ["text"]
    ends = subject.drag_end_observation(observation.candidates[0], observation)
    assert [item.subject for item in ends.candidates] == ["text", "edge"]
    assert ends.candidates[1].attributes["source"] == "edge_contour"


def test_fill_clicks_the_reidentified_region_and_types_under_the_lease():
    pointer = InputPointer()
    original = frame()
    subject, lease = input_adapter(original, original, pointer=pointer)
    observation = subject.observe()
    fill = next(candidate for candidate in observation.candidates if candidate.operation == "fill")
    result = subject.act(fill, observation, text="1366\r\nx768")
    assert (result.status, result.input_method) == ("confirmed", "synthetic_key")
    assert lease.acquired == 1
    assert pointer.checks == [(100, 7, (2, 1))]
    assert pointer.typed == [(100, 7, (2, 1), "1366\nx768")]
    assert pointer.regions == [fill.attributes["rect"]]
    assert pointer.clicks == []
    assert not result.input_focus


def test_fill_carries_the_deliverys_caret_fact_into_its_result():
    pointer = InputPointer()
    pointer.last_input_focus = True
    original = frame()
    subject, _ = input_adapter(original, original, pointer=pointer)
    observation = subject.observe()
    fill = next(candidate for candidate in observation.candidates if candidate.operation == "fill")
    assert subject.act(fill, observation, text="a").input_focus


def test_fill_rejects_missing_or_control_text_and_text_on_other_operations():
    pointer = InputPointer()
    subject, lease = input_adapter(frame(), pointer=pointer)
    observation = subject.observe()
    fill = next(candidate for candidate in observation.candidates if candidate.operation == "fill")
    click = next(candidate for candidate in observation.candidates if candidate.operation == "click")
    for candidate, text in ((fill, None), (fill, "a\x1bb"), (click, "text")):
        assert subject.act(candidate, observation, text=text).status == "not_attempted"
    assert lease.acquired == 0 and pointer.typed == pointer.clicks == []


def test_candidate_of_a_disabled_operation_is_not_attempted():
    subject, _ = input_adapter(frame(), pointer=InputPointer())
    observation = subject.observe()
    key = next(candidate for candidate in observation.candidates if candidate.operation == "key")
    subject.operations = ("click",)
    assert subject.act(key, observation).status == "not_attempted"


def test_key_is_sent_to_an_in_scope_foreground_after_the_whole_window_is_rechecked():
    root, popup = frame(), frame(hwnd=200)
    pointer = InputPointer()
    subject, lease = scoped_adapter((root, popup), (root, popup), pointer=pointer)
    subject.operations = ("key",)
    observation = subject.observe()
    escape = next(candidate for candidate in observation.candidates if candidate.attributes.get("key") == "Escape")
    assert escape.label == "Press Escape"
    result = subject.act(escape, observation)
    assert (result.status, result.input_method) == ("confirmed", "synthetic_key")
    assert pointer.focus_checks == [(100, 7, (100, 200))]
    assert pointer.keys == [(100, 7, "Escape", (100, 200))]


class FillDependentExtractor:
    def extract(self, actual):
        return (VisualRegion("r", f"fill {actual.pixels[0]}", (1, 1, 2, 1), 0.99, "template:r"),)


def test_key_survives_a_pixel_change_that_leaves_the_regions_unchanged():
    # A blinking caret changes pixels but not what the decision read (VSCode live, plan screen-input-ops).
    pointer = InputPointer()
    subject, _ = input_adapter(frame(), frame_with_unrelated_change(), frame_with_unrelated_change(),
                               pointer=pointer, operations=("key",))
    observation = subject.observe()
    assert subject.fresh(observation, observation.candidates[0]) is Freshness.FRESH
    assert subject.act(observation.candidates[0], observation).status == "confirmed"
    assert len(pointer.keys) == 1


def test_key_is_not_sent_when_the_regions_changed_or_focus_left_the_scope():
    for second, valid in ((frame_with_unrelated_change(), True), (frame(), False)):
        pointer = InputPointer(valid=valid)
        subject, _ = input_adapter(frame(), second, pointer=pointer, operations=("key",))
        subject.extractor = FillDependentExtractor()
        observation = subject.observe()
        result = subject.act(observation.candidates[0], observation)
        assert result.status == "not_attempted"
        assert pointer.keys == []


def test_unconfirmed_key_is_uncertain():
    pointer = InputPointer(confirmed=False)
    subject, _ = input_adapter(frame(), frame(), pointer=pointer, operations=("key",))
    observation = subject.observe()
    with pytest.raises(MutationUncertain):
        subject.act(observation.candidates[0], observation)


def test_fill_is_reidentified_by_region_when_its_own_caret_changed_the_anchor():
    pointer = InputPointer()
    subject, _ = input_adapter(frame(), frame(fill=2), frame(fill=2), pointer=pointer, operations=("fill",))
    observation = subject.observe()
    fill = observation.candidates[0]
    assert subject.fresh(observation, fill) is Freshness.FRESH
    assert subject.act(fill, observation, text="new").status == "confirmed"
    assert len(pointer.typed) == 1


def editor_frame(caret_width=0):
    pixels = bytearray(bytes([0, 0, 0, 255]) * 12 * 10)
    for y in range(1, 9):
        for x in range(caret_width):
            pixels[(y * 12 + x) * 4 : (y * 12 + x) * 4 + 3] = bytes([200, 200, 200])
    return WindowFrame(100, 7, 12, 10, bytes(pixels))


class CaretMisreadingExtractor:
    """OCR reads the line differently while the caret is visible (OLDVALUE -> DLDVALUE, VSCode live)."""

    def extract(self, actual):
        label = "DLDVALUE" if actual.pixels[(12 + 0) * 4] else "OLDVALUE"
        return (VisualRegion(label, label, (3, 1, 6, 8), 0.99, "template:r"),)


def test_a_caret_only_change_keeps_fill_and_key_fresh_although_ocr_reads_the_line_differently():
    for operation in ("fill", "key"):
        pointer = InputPointer()
        subject, _ = input_adapter(editor_frame(), editor_frame(2), editor_frame(2), pointer=pointer,
                                   operations=(operation,))
        subject.extractor = CaretMisreadingExtractor()
        observation = subject.observe()
        candidate = observation.candidates[0]
        assert subject.fresh(observation, candidate) is Freshness.FRESH
        text = "new" if operation == "fill" else None
        assert subject.act(candidate, observation, text=text).status == "confirmed"


def test_a_change_wider_than_a_caret_still_stops_fill_and_key():
    for operation in ("fill", "key"):
        pointer = InputPointer()
        subject, _ = input_adapter(editor_frame(), editor_frame(5), editor_frame(5), pointer=pointer,
                                   operations=(operation,))
        subject.extractor = CaretMisreadingExtractor()
        observation = subject.observe()
        candidate = observation.candidates[0]
        assert subject.fresh(observation, candidate) is Freshness.STALE
        text = "new" if operation == "fill" else None
        assert subject.act(candidate, observation, text=text).status == "not_attempted"
        assert pointer.typed == [] and pointer.keys == []


def list_frame(fill=0, *, hwnd=100):
    return WindowFrame(hwnd, 7, 100, 100, bytes([fill, fill, fill, 255]) * 10000)


class ListExtractor:
    """A narrow list of rows under a window-wide header, a two-row pair and an edge box."""

    def __init__(self, rows=("alpha", "beta", "gamma", "delta")):
        self.rows = rows

    def extract(self, actual):
        regions = [VisualRegion("header", "File Edit View", (0, 0, 90, 8), 0.9, "ocr:header")]
        regions += [
            VisualRegion(f"row{index}", label, (10, 12 + index * 10, 30, 8), 0.9, f"ocr:{label}")
            for index, label in enumerate(self.rows)
        ]
        regions += [
            VisualRegion("ok", "OK", (60, 20, 20, 8), 0.9, "ocr:ok"),
            VisualRegion("cancel", "Cancel", (60, 30, 20, 8), 0.9, "ocr:cancel"),
            VisualRegion("box", "box", (5, 10, 40, 60), 0.5, "edge_contour:pixels=1"),
        ]
        return tuple(regions)


class ScrollPointer(InputPointer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.scrolls = []

    def scroll(self, *, hwnd, process_id, point, notches):
        self.scrolls.append((hwnd, process_id, point, notches))
        return self.confirmed


def scroll_adapter(*frames, pointer, extractor=None):
    subject, lease = input_adapter(*frames, pointer=pointer, operations=("click", "scroll"))
    subject.extractor = extractor or ListExtractor()
    subject._sleep = lambda _seconds: None
    return subject, lease


def scrolls_of(observation):
    return [candidate for candidate in observation.candidates if candidate.operation == "scroll"]


def test_scroll_is_offered_up_and_down_only_for_a_list_like_stack_of_rows():
    subject, _ = scroll_adapter(list_frame(), pointer=ScrollPointer())
    scrolls = scrolls_of(subject.observe())
    assert [candidate.attributes["direction"] for candidate in scrolls] == ["up", "down"]
    assert scrolls[1].label == "Scroll down the list from 'alpha' to 'delta'"
    assert scrolls[1].attributes["rect"] == (10, 12, 30, 38)
    assert scrolls[1].attributes["notches"] == 1
    assert "notches" in json.dumps(scrolls[1].provider_record())
    click_only, _ = input_adapter(list_frame(), pointer=ScrollPointer(), operations=("click",))
    click_only.extractor = ListExtractor()
    assert scrolls_of(click_only.observe()) == []


def test_scroll_wheels_at_the_list_center_after_the_whole_window_is_rechecked():
    pointer = ScrollPointer()
    subject, lease = scroll_adapter(list_frame(), list_frame(), pointer=pointer)
    observation = subject.observe()
    down = scrolls_of(observation)[1]
    result = subject.act(down, observation)
    assert (result.status, result.input_method) == ("confirmed", "synthetic_pointer")
    assert lease.acquired == 1
    assert pointer.checks == [(100, 7, (25, 31))]
    assert pointer.scrolls == [(100, 7, (25, 31), -1)]


def test_scroll_is_not_sent_when_the_list_changed_before_delivery():
    pointer = ScrollPointer()
    subject, _ = scroll_adapter(list_frame(), list_frame(fill=1), pointer=pointer)

    class Shifting(ListExtractor):
        calls = 0

        def extract(self, actual):
            Shifting.calls += 1
            self.rows = ("alpha", "beta", "gamma", "delta") if Shifting.calls == 1 else ("beta", "gamma", "delta", "eps")
            return super().extract(actual)

    subject.extractor = Shifting()
    observation = subject.observe()
    assert subject.act(scrolls_of(observation)[1], observation).status == "not_attempted"
    assert pointer.scrolls == []


def test_a_scroll_that_changed_nothing_drops_that_direction_until_the_list_moves_again():
    pointer = ScrollPointer()
    still, moved = list_frame(), list_frame(fill=1)
    # observe, act recapture, observe + settle recapture (unchanged: end reached),
    # act recapture for up, observe (moved), then one more observe.
    subject, _ = scroll_adapter(still, still, still, still, still, moved, moved, pointer=pointer)
    observation = subject.observe()
    assert subject.act(scrolls_of(observation)[1], observation).status == "confirmed"
    observation = subject.observe()
    assert [candidate.attributes["direction"] for candidate in scrolls_of(observation)] == ["up"]
    assert subject.act(scrolls_of(observation)[0], observation).status == "confirmed"
    observation = subject.observe()
    assert [candidate.attributes["direction"] for candidate in scrolls_of(observation)] == ["up", "down"]
    assert [notches for *_, notches in pointer.scrolls] == [-1, 1]


def test_a_slow_repaint_after_the_wheel_is_not_taken_as_the_end():
    pointer = ScrollPointer()
    still, moved = list_frame(), list_frame(fill=1)
    subject, _ = scroll_adapter(still, still, still, moved, pointer=pointer)
    observation = subject.observe()
    subject.act(scrolls_of(observation)[1], observation)
    assert len(scrolls_of(subject.observe())) == 2


def test_screen_evidence_returns_the_observed_frame_and_its_regions():
    before, after = frame(), frame(9)
    subject, _ = adapter(before, after)
    first = subject.observe()
    second = subject.observe()
    candidate = first.candidates[0]
    evidence = subject.screen_evidence(first, candidate)
    assert evidence.frame is before
    assert [region.label for region in evidence.regions] == ["Game view resolution"]
    assert subject.screen_evidence(second, candidate).frame is after


def test_unchanged_compares_the_observed_pixels_not_the_ocr_text():
    subject, _ = adapter(frame(), frame(), frame_with_unrelated_change())
    first, same, changed = subject.observe(), subject.observe(), subject.observe()
    assert subject.unchanged(first, same)
    assert not subject.unchanged(first, changed)


def test_key_reticle_frames_the_focused_element_not_the_whole_window():
    """E2E-I7: a key on the desktop framed the full screen."""

    class Indicator:
        rects = []

        def show(self):
            pass

        def close(self):
            pass

        def phase(self, name):
            pass

        @contextmanager
        def reticle(self, *, hwnd, rect, action):
            Indicator.rects.append((rect, action))
            yield

    class FocusPointer(InputPointer):
        def focus_target(self, *, hwnd, process_id, foreground_hwnds):
            valid = self.focus_is_valid(hwnd=hwnd, process_id=process_id, foreground_hwnds=foreground_hwnds)
            return valid, (1, 0, 9, 9)

    subject, _ = input_adapter(frame(), frame(), pointer=FocusPointer(), operations=("key",))
    subject.indicator = Indicator()
    observation = subject.observe()
    assert subject.act(observation.candidates[0], observation).status == "confirmed"
    assert Indicator.rects == [((1, 0, 3, 3), "key")]


def uia_adapter(*frames, reads, pointer=None, operations=("click", "fill")):
    from finitact.windows_uia_reader import UiaElement

    answers = iter(reads)

    class CountingExtractor(Extractor):
        calls = 0

        def extract(self, actual):
            CountingExtractor.calls += 1
            return super().extract(actual)

    def reader(_hwnd):
        return tuple(UiaElement(*element) for element in next(answers))

    subject, lease = input_adapter(*frames, pointer=pointer or InputPointer(), operations=operations)
    subject.extractor = CountingExtractor()
    subject.uia_reader = reader
    return subject, lease, CountingExtractor


SEND_BOX = ("edit", "Message #test", (0, 1, 4, 1), True, "draft")
CHANNEL = ("link", "test", (0, 0, 2, 1), False, None)


def test_uia_elements_become_candidates_without_ocr_and_fill_carries_the_current_value_apart():
    """ADR-0036: UIA names replace OCR text for candidates."""
    subject, _, extractor = uia_adapter(frame(), reads=[(CHANNEL, SEND_BOX)])
    observation = subject.observe()
    assert extractor.calls == 0
    kinds = sorted((candidate.operation, candidate.label) for candidate in observation.candidates)
    assert kinds == [("click", "Message #test"), ("click", "test"), ("fill", "Message #test")]
    fill = next(candidate for candidate in observation.candidates if candidate.operation == "fill")
    assert observation.untrusted_values == {fill.id: "draft"}
    assert subject.screen_evidence(observation, fill) is not None and extractor.calls == 0


def _checked(element):
    return ("checkbox", element[0], (0, 0, 2, 1), False, None, False, False, None, element[1], element[2])


def test_uia_toggle_and_selection_item_effects_compare_the_expected_state_adr_0039():
    flipped = ("VPN", "true", False), ("VPN", "false", False)
    refused = ("VPN", "true", False), ("VPN", "true", False)
    selected = ("Large", "false", True), ("Large", "true", True)
    for (before, after), effect in ((flipped, "met"), (refused, "not_met"), (selected, "met")):
        subject, _, _ = uia_adapter(frame(), frame(), reads=[(_checked(before),), (_checked(after),)], operations=("click",))
        first = subject.observe()
        second = subject.observe()
        result = subject.effect(first.candidates[0], first, second, None)
        assert result["effect"] == effect and result["expected"] == {"checked": "true" if before[2] else "false"}
        assert first.candidates[0].attributes["checked"] == before[1]
        assert (first.semantic_id == second.semantic_id) == (effect == "not_met")


def test_a_repainted_window_with_an_unchanged_toggle_stays_fresh_for_done():
    vpn = _checked(("VPN", "true", False))
    subject, _, _ = uia_adapter(frame(), frame(9), reads=[(vpn,), (vpn,)], operations=("click",))
    assert subject.fresh(subject.observe()) == Freshness.FRESH


def test_uia_click_without_a_state_has_no_effect():
    subject, _, _ = uia_adapter(frame(), frame(), reads=[(CHANNEL,), (CHANNEL,)], operations=("click",))
    first, second = subject.observe(), subject.observe()
    assert subject.effect(first.candidates[0], first, second, None) is None


def test_uia_evidence_is_read_without_ocr_and_shows_a_field_value_that_changed_under_the_same_name():
    # consult 20260925-2134 point 5: a fill keeps the field's name, so only its value shows the text landed.
    empty = ("edit", "Email", (0, 1, 4, 1), True, "")
    filled = ("edit", "Email", (0, 1, 4, 1), True, "user@example.com")
    hidden = ("listitem", "Far", (0, 2, 2, 1), False, None, True)
    subject, _, extractor = uia_adapter(frame(), frame(9), reads=[(CHANNEL, empty, hidden), (CHANNEL, filled, hidden)])
    before, after = subject.observe(), subject.observe()
    fill = next(candidate for candidate in before.candidates if candidate.operation == "fill")
    labels = lambda observation: sorted(r.label for r in subject.screen_evidence(observation, fill).regions)  # noqa: E731
    assert labels(before) == ["Email", "test"]
    assert labels(after) == ["Email", "test", "user@example.com"]
    assert extractor.calls == 0


def test_a_field_value_change_is_a_semantic_change():
    # E2E-03: a suggestion click filled the Email field; with labels unchanged the click was repeated 3 times.
    empty = ("edit", "Email", (0, 1, 4, 1), True, "")
    filled = ("edit", "Email", (0, 1, 4, 1), True, "user@example.com")
    subject, _, _ = uia_adapter(frame(), frame(), reads=[(CHANNEL, empty), (CHANNEL, filled)])
    assert subject.observe().semantic_id != subject.observe().semantic_id


def test_a_field_holding_a_value_stays_fresh_when_uia_reads_it_again():
    filled = ("edit", "Email", (0, 1, 4, 1), True, "user@example.com")
    subject, _, _ = uia_adapter(frame(), frame(), reads=[(CHANNEL, filled), (CHANNEL, filled)])
    assert subject.fresh(subject.observe()) is Freshness.FRESH


def test_uia_candidate_is_reread_before_input_and_a_vanished_element_is_not_clicked():
    pointer = InputPointer()
    subject, _, _ = uia_adapter(frame(), frame(), reads=[(CHANNEL,), ()], pointer=pointer, operations=("click",))
    observation = subject.observe()
    result = subject.act(observation.candidates[0], observation)
    assert result.status == "not_attempted" and pointer.clicks == []


def test_uia_candidate_is_clicked_when_the_reread_finds_it_once_even_if_pixels_changed():
    pointer = InputPointer()
    subject, _, _ = uia_adapter(frame(), frame(9), reads=[(CHANNEL,), (CHANNEL,)], pointer=pointer, operations=("click",))
    observation = subject.observe()
    assert subject.act(observation.candidates[0], observation).status == "confirmed"
    assert len(pointer.clicks) == 1


def test_a_window_without_uia_elements_keeps_the_ocr_path():
    subject, _, extractor = uia_adapter(frame(), reads=[()], operations=("click",))
    observation = subject.observe()
    assert extractor.calls == 1 and observation.candidates[0].label == "Game view resolution"


VOLUME = ("slider", "Volume", (0, 2, 4, 1), False, "47", False, False, (0.0, 100.0))


def test_uia_slider_is_offered_as_set_range_with_its_number_instead_of_a_pointer_target():
    """BUG-0056: the outer agent aimed at a volume slider's thumb by pixels for minutes."""
    subject, _, _ = uia_adapter(frame(), reads=[(CHANNEL, VOLUME)], operations=("click", "set_range"))
    observation = subject.observe()
    kinds = sorted((candidate.operation, candidate.label) for candidate in observation.candidates)
    assert kinds[-2:] == [("click", "test"), ("set_range", "Volume")] and ("click", "Volume") not in kinds
    slider = next(candidate for candidate in observation.candidates if candidate.operation == "set_range")
    assert observation.untrusted_values == {slider.id: "47"} and slider.attributes["range"] == "0..100"


def test_set_range_sets_the_reread_slider_through_uia_and_refuses_what_is_not_a_number_in_range():
    moved = VOLUME[:4] + ("30",) + VOLUME[5:]
    subject, _, _ = uia_adapter(frame(), frame(), reads=[(VOLUME,), (VOLUME,)], operations=("set_range",))
    set_to = []
    subject.uia_range_setter = lambda hwnd, name, number: set_to.append((name, number)) or True
    observation = subject.observe()
    slider = observation.candidates[0]
    assert subject.act(slider, observation, "abc").detail == "set_range requires a number"
    assert subject.act(slider, observation, "101").status == "not_attempted"
    assert subject.act(slider, observation).status == "not_attempted"
    result = subject.act(slider, observation, "30")
    assert (result.status, result.input_method, set_to) == ("confirmed", "pattern", [("Volume", 30.0)])
    after, _, _ = uia_adapter(frame(), reads=[(moved,)], operations=("set_range",))
    assert after.observe().semantic_id != observation.semantic_id


def test_set_range_is_not_sent_when_the_slider_is_gone_on_reread():
    subject, _, _ = uia_adapter(frame(), frame(), reads=[(VOLUME,), ()], operations=("set_range",))
    set_to = []
    subject.uia_range_setter = lambda *args: set_to.append(args) or True
    observation = subject.observe()
    assert subject.act(observation.candidates[0], observation, "30").status == "not_attempted" and set_to == []


def test_offscreen_uia_item_is_offered_then_scrolled_in_and_clicked_at_its_new_place():
    """ADR-0038: an item scrolled out of its list is observed through UIA and scrolled in before input."""
    from finitact.windows_uia_reader import UiaElement

    hidden = UiaElement("listitem", "Tokyo", (0, 2, 2, 1), False, None, True)
    shown = UiaElement("listitem", "Tokyo", (2, 0, 2, 1), False, None, False)
    reads = iter([(hidden,), (shown,)])
    scrolled = []

    class GesturePointer(InputPointer):
        def click(self, *, hwnd, process_id, point, **kwargs):
            self.clicks.append(point)
            return True

    pointer = GesturePointer()
    subject, _ = input_adapter(frame(), frame(), frame(), pointer=pointer, operations=("click",))
    subject.uia_reader = lambda _hwnd: next(reads)
    subject.uia_scroller = lambda hwnd, role, name: scrolled.append((role, name))
    observation = subject.observe()
    candidate = observation.candidates[0]
    assert candidate.attributes["offscreen"] == "scrolled into view before input"
    assert subject.act(candidate, observation).status == "confirmed"
    assert scrolled == [("listitem", "Tokyo")] and pointer.clicks == [(3, 0)]


def test_offscreen_uia_item_is_not_clicked_when_the_scroll_finds_it_twice():
    from finitact.windows_uia_reader import UiaElement

    hidden = UiaElement("listitem", "Tokyo", (0, 2, 2, 1), False, None, True)
    twice = (UiaElement("listitem", "Tokyo", (2, 0, 2, 1), False, None), UiaElement("listitem", "Tokyo", (0, 0, 1, 1), False, None))
    reads = iter([(hidden,), twice])
    pointer = InputPointer()
    subject, _ = input_adapter(frame(), frame(), frame(), pointer=pointer, operations=("click",))
    subject.uia_reader = lambda _hwnd: next(reads)
    subject.uia_scroller = lambda *args: None
    observation = subject.observe()
    assert subject.act(observation.candidates[0], observation).status == "not_attempted"
    assert pointer.clicks == []


def test_uia_field_the_form_rejected_is_marked_invalid():
    """Validation: the input went in but the form did not accept it (yo-xe, E2E-02)."""
    rejected = ("edit", "Requests", (0, 1, 4, 1), True, "abc", False, True)
    subject, _, _ = uia_adapter(frame(), reads=[(rejected,)], operations=("fill",))
    fill = subject.observe().candidates[0]
    assert fill.attributes["invalid"] == "value rejected by the form"


def test_hit_test_failure_names_what_the_pointer_saw():
    pointer = Pointer(valid=False)
    pointer.last_refusal = "point is covered by 42 ConsoleWindowClass 'PowerShell'"
    original = frame()
    subject, _ = adapter(original, original, pointer=pointer)
    observation = subject.observe()
    result = subject.act(observation.candidates[0], observation)
    assert result.status == "not_attempted"
    assert result.detail == "delivery target verification failed: point is covered by 42 ConsoleWindowClass 'PowerShell'"
    assert pointer.clicks == []


def region_uia_adapter(*frames, reads, found, pointer=None):
    """ADR-0044: a UIA window whose uncovered right half is read by OCR; ``found`` is in crop coordinates."""
    from finitact.windows_uia_reader import UiaElement

    answers = iter(reads)
    crops = []

    class CropExtractor:
        def extract(self, actual):
            crops.append((actual.width, actual.height))
            return next(found)

    subject, _ = input_adapter(*frames, pointer=pointer or InputPointer(), operations=("click",))
    subject.extractor = CropExtractor()
    subject.uia_reader = lambda _hwnd: tuple(UiaElement(*element) for element in next(answers))
    return subject, crops


def wide_frame(shade=0):
    import numpy as np

    pixels = np.full((64, 128, 4), 255, np.uint8)
    pixels[16:40, 80:112:3, :3] = shade
    return WindowFrame(100, 7, 128, 64, pixels.tobytes())


SIDEBAR = ("link", "Inbox", (0, 0, 64, 64), False, None)


def test_uia_window_offers_ocr_text_only_where_uia_leaves_the_window_uncovered():
    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    inside_uia = VisualRegion("ocr-inbox", "Inbox", (-60, 8, 20, 10), 0.9, "ocr:1")
    subject, crops = region_uia_adapter(wide_frame(), reads=[(SIDEBAR,)], found=iter([(editor, inside_uia)]))
    observation = subject.observe()
    assert len(crops) == 1 and crops[0][0] < 128
    labels = {candidate.label: candidate for candidate in observation.candidates}
    assert set(labels) == {"Inbox", "OLDVALUE"}
    rect = labels["OLDVALUE"].attributes["rect"]
    assert rect[0] >= 64 and rect[2:] == (30, 10)


def test_ocr_candidate_in_a_uia_window_is_clicked_only_after_region_ocr_finds_it_again():
    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    pointer = InputPointer()
    subject, _ = region_uia_adapter(
        wide_frame(), wide_frame(1), reads=[(SIDEBAR,), (SIDEBAR,)], found=iter([(editor,), ()]), pointer=pointer
    )
    observation = subject.observe()
    target = next(candidate for candidate in observation.candidates if candidate.label == "OLDVALUE")
    assert subject.act(target, observation).status == "not_attempted" and pointer.clicks == []


def test_uia_candidate_reidentification_uses_uia_without_rereading_uncovered_ocr():
    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    pointer = InputPointer()
    subject, crops = region_uia_adapter(
        wide_frame(), wide_frame(1), wide_frame(1),
        reads=[(SIDEBAR,)] * 3, found=iter([(editor,)]), pointer=pointer,
    )  # fmt: skip
    observation = subject.observe()
    inbox = next(candidate for candidate in observation.candidates if candidate.label == "Inbox")
    assert subject.act(inbox, observation).status == "confirmed"
    assert len(pointer.clicks) == 1
    assert len(crops) == 1  # The initial complete observation still read the OCR candidate.


def test_drag_in_a_uia_window_takes_its_ends_from_uia_and_uncovered_ocr_and_finds_both_again():
    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    for found_again, expected in (((editor,), "confirmed"), ((), "not_attempted")):
        pointer = DragPointer()
        subject, crops = region_uia_adapter(
            wide_frame(), wide_frame(1), reads=[(SIDEBAR,)] * 2, found=iter([(editor,), found_again]), pointer=pointer
        )
        subject.operations = ("drag",)
        observation = subject.observe()
        start = next(candidate for candidate in observation.candidates if "Inbox" in candidate.label)
        ends = subject.drag_end_observation(start, observation)
        end = next(candidate for candidate in ends.candidates if "OLDVALUE" in candidate.label)
        assert len(crops) == 1
        assert subject.act(end, ends).status == expected
        assert len(pointer.drags) == (expected == "confirmed")


def test_a_uia_window_without_an_editable_element_keeps_uia_and_takes_fill_from_whole_frame_ocr():
    """BUG-0040: the taskbar's buttons exist only in UIA; VSCode's Search text lies under a UIA button (ADR-0036 追記2)."""
    editor = VisualRegion("ocr-old", "OLDVALUE", (80, 8, 30, 10), 0.9, "ocr:1")
    under_button = VisualRegion("ocr-search", "Search", (8, 8, 30, 10), 0.9, "ocr:1")
    subject, crops = region_uia_adapter(wide_frame(), reads=[(SIDEBAR,)], found=iter([(editor, under_button)]))
    subject.operations = ("click", "fill")
    observation = subject.observe()
    assert crops == [(128, 64)]
    fills = {candidate.label for candidate in observation.candidates if candidate.operation == "fill"}
    clicks = {candidate.label for candidate in observation.candidates if candidate.operation == "click"}
    assert fills == {"OLDVALUE", "Search"} and clicks == {"Inbox", "OLDVALUE"}


def test_an_ocr_fill_in_a_uia_window_is_typed_only_after_region_ocr_finds_it_again():
    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    for found_again, expected in (((editor,), "confirmed"), ((), "not_attempted")):
        pointer = InputPointer()
        subject, _ = region_uia_adapter(
            wide_frame(), wide_frame(1), reads=[(SIDEBAR,)] * 2, found=iter([(editor,), found_again]), pointer=pointer
        )
        subject.operations = ("click", "fill")
        observation = subject.observe()
        fill = next(candidate for candidate in observation.candidates if candidate.operation == "fill")
        assert subject.act(fill, observation, "new").status == expected


def test_an_ocr_fill_in_a_uia_window_keeps_its_observed_reading_through_a_caret_blink():
    import numpy as np

    editor = VisualRegion("ocr-old", "OLDVALUE", (80, 8, 30, 10), 0.9, "ocr:1")
    blink = np.frombuffer(wide_frame().pixels, np.uint8).reshape(64, 128, 4).copy()
    blink[20:36, 120, :3] = 0
    blinked = WindowFrame(100, 7, 128, 64, blink.tobytes())
    misread = VisualRegion("ocr-old", "DLDVALUE", (80, 8, 30, 10), 0.9, "ocr:1")
    subject, _ = region_uia_adapter(
        wide_frame(), blinked, blinked, reads=[(SIDEBAR,)] * 3, found=iter([(editor,)] + [(misread,)] * 2)
    )
    subject.operations = ("click", "fill")
    observation = subject.observe()
    fill = next(candidate for candidate in observation.candidates if candidate.operation == "fill")
    assert subject.fresh(observation, fill) is Freshness.FRESH
    assert subject.act(fill, observation, "new").status == "confirmed"


def test_evidence_for_an_ocr_offered_fill_stays_ocr_after_an_editable_field_appears():
    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    quick_input = ("edit", "Search", (0, 0, 64, 10), True, "")
    subject, crops = region_uia_adapter(
        wide_frame(), wide_frame(), reads=[(SIDEBAR,), (SIDEBAR, quick_input)], found=iter([(editor,)] * 4)
    )
    subject.operations = ("click", "fill")
    before = subject.observe()
    fill = next(candidate for candidate in before.candidates if candidate.operation == "fill")
    after = subject.observe()
    calls = len(crops)
    evidence = subject.screen_evidence(after, fill)
    assert len(crops) == calls + 1 and crops[-1] == (128, 64)
    assert {region.evidence.split(":", 1)[0] for region in evidence.regions} == {
        region.evidence.split(":", 1)[0] for region in subject.screen_evidence(before, fill).regions}


def test_a_caret_blink_keeps_the_observed_region_ocr_reading():
    import numpy as np

    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    blink = np.frombuffer(wide_frame().pixels, np.uint8).reshape(64, 128, 4).copy()
    blink[20:36, 120, :3] = 0
    subject, crops = region_uia_adapter(
        wide_frame(),
        WindowFrame(100, 7, 128, 64, blink.tobytes()),
        reads=[(SIDEBAR,), (SIDEBAR,)],
        found=iter([(editor,), (VisualRegion("ocr-old", "DLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1"),)]),
    )
    observation = subject.observe()
    assert subject.fresh(observation) is Freshness.FRESH and len(crops) == 1


def test_uncovered_ocr_noise_leaves_a_uia_candidate_fresh_but_not_an_ocr_one():
    editor = VisualRegion("ocr-old", "OLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    misread = VisualRegion("ocr-old", "DLDVALUE", (8, 8, 30, 10), 0.9, "ocr:1")
    first, _ = region_uia_adapter(
        wide_frame(), wide_frame(1), wide_frame(1),
        reads=[(SIDEBAR,)] * 3, found=iter([(editor,), (misread,), (misread,)]),
    )  # fmt: skip
    observation = first.observe()
    labels = {candidate.label: candidate for candidate in observation.candidates}
    # E2E-I46: the MCP ref path judges freshness in a second adapter that adopted the observation.
    second, crops = region_uia_adapter(wide_frame(1), wide_frame(1), reads=[(SIDEBAR,)] * 2, found=iter([(misread,)] * 2))
    second.adopt(observation, first.retain(observation))
    assert second.fresh(observation, labels["Inbox"]) is Freshness.FRESH
    assert crops == []  # The UIA-only freshness verdict does not use uncovered OCR.
    assert second.fresh(observation, labels["OLDVALUE"]) is Freshness.STALE


def test_ocr_popup_of_a_uia_window_is_judged_by_its_own_frame():
    from finitact.windows_uia_reader import UiaElement

    # E2E-I46: observe_window allows fill, so an autofill list without an editable element is read by OCR while
    # the root stays UIA; a validation tooltip beside it may vanish before the pick.
    root, popup, tooltip = frame(), frame(hwnd=200), frame(hwnd=300)
    subject, _ = scoped_adapter(
        (root, popup, tooltip), (root, popup), (root, frame(fill=9, hwnd=200)), pointer=ScopedPointer()
    )
    subject.uia_reader = lambda hwnd: (UiaElement("edit", "Email", (0, 0, 4, 2), True, ""),) if hwnd == root.hwnd else ()
    subject.operations = ("click", "fill")
    observation = subject.observe()
    full_hd = next(c for c in observation.candidates if c.label == "Full HD" and c.operation == "click")
    assert subject.fresh(observation, full_hd) is Freshness.FRESH
    assert subject.fresh(observation, full_hd) is Freshness.STALE


class CrossDragPointer(DragPointer):
    def target_is_valid(self, *, hwnd, process_id, point, foreground_hwnds=None):
        self.checks.append((hwnd, process_id, point, foreground_hwnds))
        return self.valid

    def drag(self, *, hwnd, process_id, start, end, foreground_hwnds=None, end_hwnd=None, end_process_id=None):
        self.drags.append((hwnd, process_id, start, end, end_hwnd, end_process_id))
        return self.confirmed


def cross_adapter(*, drop_frames, pointer, root_frames=None):
    root_frames = root_frames or [frame()] * 4
    subject, lease = adapter(*root_frames, pointer=pointer)
    subject.operations = ("drag",)
    subject.drop_captures = (Capture(*drop_frames),)
    return subject, lease


def test_cross_window_drag_offers_drop_ends_only_at_end_and_delivers_each_endpoint_in_its_window():
    pointer = CrossDragPointer()
    drop = frame(hwnd=200, process_id=9)
    subject, lease = cross_adapter(drop_frames=[drop] * 3, pointer=pointer)
    observation = subject.observe()
    assert {c.attributes.get("scope_hwnd") for c in observation.candidates} == {None}
    ends = subject.drag_end_observation(observation.candidates[0], observation)
    drop_end = next(c for c in ends.candidates if c.attributes.get("scope") == "drop_window")
    assert drop_end.attributes["scope_hwnd"] == 200 and "drop window" in drop_end.label
    assert subject.fresh(ends, drop_end) == Freshness.FRESH
    assert subject.act(drop_end, ends).status == "confirmed"
    assert pointer.drags == [(100, 7, (2, 1), (2, 1), 200, 9)]
    assert [check[:2] for check in pointer.checks] == [(100, 7), (200, 9)]


def test_cross_window_drag_is_refused_before_delivery_when_the_drop_window_changed():
    pointer = CrossDragPointer()
    subject, lease = cross_adapter(
        drop_frames=[frame(hwnd=200, process_id=9), frame(hwnd=200, process_id=9, fill=9)], pointer=pointer
    )
    class Changed(Extractor):
        def __init__(self):
            self.calls = 0

        def extract(self, actual):
            self.calls += 1
            label = "Game view resolution" if self.calls < 3 else "Other"
            return (VisualRegion("resolution", label, (1, 1, 2, 1), 0.99, "template:resolution"),)

    subject.extractor = Changed()
    observation = subject.observe()
    ends = subject.drag_end_observation(observation.candidates[0], observation)
    drop_end = next(c for c in ends.candidates if c.attributes.get("scope") == "drop_window")
    result = subject.act(drop_end, ends)
    assert result.status == "not_attempted" and pointer.drags == []


def test_drop_window_that_is_a_different_window_identity_makes_the_end_stale():
    subject, _ = cross_adapter(
        drop_frames=[frame(hwnd=200, process_id=9), frame(hwnd=201, process_id=9)], pointer=CrossDragPointer()
    )
    observation = subject.observe()
    ends = subject.drag_end_observation(observation.candidates[0], observation)
    drop_end = next(c for c in ends.candidates if c.attributes.get("scope") == "drop_window")
    assert subject.fresh(ends, drop_end) == Freshness.STALE


def test_drop_window_inside_the_run_scope_is_rejected():
    subject, _ = cross_adapter(drop_frames=[frame(hwnd=100)], pointer=CrossDragPointer())
    observation = subject.observe()
    with pytest.raises(ScopeViolation):
        subject.drag_end_observation(observation.candidates[0], observation)
