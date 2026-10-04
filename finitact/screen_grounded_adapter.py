"""Screen-grounded ActionAdapter with lease-guarded pointer delivery.

The adapter deliberately keeps capture, visual extraction, native target checks and pointer
delivery as separate seams.  A visual extractor proposes a finite set of regions; it never
authorizes input.  Immediately before delivery the adapter captures a new target-window frame
under the interaction lease and requires both the whole frame and the selected anchor to match
the observation used by the decision provider.
"""

from __future__ import annotations

import hashlib
import time
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Protocol, Sequence

import numpy as np

from .action_adapter import Freshness, MutationResult, Observation, ScopeViolation, checked_effect
from .contracts import EDGE_CONTOUR_SOURCE, FILL_PANEL_SOURCE, ObservedCandidate
from .fill_panels import panel_blocks
from .interaction_lease import SyntheticInputGuard
from .screen_cover import uncovered_crop

Rect = tuple[int, int, int, int]

# ADR-0033: pointer gestures on one region. Each is offered only when requested, since every region
# yields one candidate per offered gesture.
POINTER_OPERATIONS = ("click", "double_click", "right_click", "middle_click", "hover", "ctrl_click", "shift_click")
# set_range is a UIA RangeValuePattern call on a slider (BUG-0056), delivered under the same gates.
SCREEN_OPERATIONS = (*POINTER_OPERATIONS, "fill", "key", "scroll", "drag", "set_range")
# The outer agent sees labels cut to this length (runs.UNCERTAIN_LABEL_CHARS).
FILL_PANEL_LABEL_CHARS = 120
# A text caret at up to 200% scaling; VSCode at 125% measured 2x19px.
CARET_MAX_WIDTH = 4
# A caption and the field it names (BUG-0034: Tk "Name:" 13px left of a 20px-high Entry).
FIELD_LABEL_MAX_CHARS = 24
FIELD_LABEL_MAX_GAP_LINES = 2
CARET_MAX_HEIGHT = 64
# OCR text under a named UIA element, kept for fills only (ADR-0036 追記2, BUG-0040).
COVERED_SUFFIX = ":covered"
# A fixed allowlist keeps a key candidate's meaning reviewable; free-form chords are not offered
# (docs/plans/screen-input-ops.md step 1). ADR-0033: every key whose effect stays inside the target
# window; Win chords, Alt+Tab and Alt+F4 (shutdown dialog on the desktop) act outside it.
KEY_ALLOWLIST = (
    "Enter", "Escape", "Tab", "Shift+Tab", "Up", "Down", "Left", "Right",
    "Home", "End", "PageUp", "PageDown", "Ctrl+A", "Ctrl+S", "Ctrl+Z",
    "Space", "Backspace", "Delete", "Insert", "Shift+Enter", "Ctrl+Enter",
    "Shift+Up", "Shift+Down", "Shift+Left", "Shift+Right", "Shift+Home", "Shift+End",
    "Ctrl+Left", "Ctrl+Right", "Ctrl+Home", "Ctrl+End", "Ctrl+Backspace", "Ctrl+Delete",
    "Ctrl+C", "Ctrl+X", "Ctrl+V", "Ctrl+Y", "Ctrl+Shift+Z",
    "Ctrl+F", "Ctrl+N", "Ctrl+O", "Ctrl+T", "Ctrl+W", "Ctrl+Tab", "Ctrl+Shift+Tab",
    "Alt", "Shift+F10", "Apps",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
)
# A scroll candidate is a list-like stack of text rows, not every region x direction, which would
# triple the candidates (plan screen-input-ops step 2). Window-wide rows (toolbars, headers) would
# chain unrelated stacks together.
SCROLL_MIN_ROWS = 3
SCROLL_MAX_GAP_ROWS = 1.5
SCROLL_MAX_WIDTH_RATIO = 0.6
SCROLL_MAX_NOTCHES = 10
# Smooth-scrolling apps repaint after the wheel returns; an unchanged frame is rechecked once
# before the direction is taken as exhausted.
SCROLL_SETTLE_SECONDS = 0.3


class DeliveryIndicator(Protocol):
    """ADR-0010's visible indicator, as seen from the platform-neutral adapter."""

    def show(self) -> None: ...

    def close(self) -> None: ...

    def reticle(self, *, hwnd: int, rect: Rect, action: str) -> AbstractContextManager: ...


class _NoOpIndicator:
    def show(self) -> None:
        return None

    def close(self) -> None:
        return None

    def reticle(self, *, hwnd: int, rect: Rect, action: str) -> AbstractContextManager:
        return nullcontext()


@dataclass(frozen=True)
class WindowFrame:
    hwnd: int
    process_id: int
    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if self.hwnd <= 0 or self.process_id <= 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("frame must identify a positive HWND, PID and size")
        if len(self.pixels) != self.width * self.height * 4:
            raise ValueError("frame pixels must be tightly packed BGRA")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()

    def anchor_fingerprint(self, rect: Rect) -> str:
        left, top, width, height = _checked_rect(rect, self.width, self.height)
        rows = []
        stride = self.width * 4
        for y in range(top, top + height):
            start = y * stride + left * 4
            rows.append(self.pixels[start : start + width * 4])
        return hashlib.sha256(b"".join(rows)).hexdigest()


@dataclass(frozen=True)
class VisualRegion:
    id: str
    label: str
    rect: Rect
    confidence: float
    evidence: str

    def __post_init__(self) -> None:
        if not self.id or not self.label or not self.evidence:
            raise ValueError("visual regions require id, label and evidence")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("visual region confidence must be between zero and one")


@dataclass(frozen=True)
class ScreenEvidence:
    """One observed frame as the achievement judgment reads it (finitact/achievement.py)."""

    frame: WindowFrame
    regions: tuple[VisualRegion, ...]
    panels: tuple[tuple[Rect, tuple[str, ...]], ...]
    captions: tuple[tuple[str, str], ...]
    row_stacks: tuple[Rect, ...]


@dataclass(frozen=True)
class RetainedScreen:
    """What one observation needs to be judged and acted on by another adapter (ADR-0041).

    Frames alone sent an adopted UIA observation down the pixel and OCR paths: fresh() compared an OCR re-read
    with a UIA semantic id and the achievement judgment found no evidence.
    """

    frames: tuple[WindowFrame, ...]
    regions: tuple[tuple[VisualRegion, ...], ...] | None = None
    uia_hwnds: frozenset[int] = frozenset()
    values: tuple[tuple[VisualRegion, ...], ...] = ()
    ocr_candidates: frozenset[str] = frozenset()
    uia_semantic_id: str | None = None


DROP_SCOPE = "drop_window"


@dataclass(frozen=True)
class _DropScope:
    frames: tuple[WindowFrame, ...]
    regions: tuple[tuple[VisualRegion, ...], ...]
    uia_hwnds: frozenset[int]


class FrameCapture(Protocol):
    def capture(self) -> WindowFrame: ...


class ScopeCapture(FrameCapture, Protocol):
    """Optional seam: the root frame first, then its visible owned top-level popups (ADR-0012 A).

    Implementations include only windows whose owner chain reaches the root; same-PID windows
    outside that chain are never part of the run's scope.
    """

    def capture_scope(self) -> Sequence[WindowFrame]: ...


class VisualCandidateExtractor(Protocol):
    def extract(self, frame: WindowFrame) -> Sequence[VisualRegion]: ...


class PointerDelivery(Protocol):
    """``foreground_hwnds`` is passed only for owned-popup targets; otherwise foreground must be ``hwnd``."""

    def target_is_valid(self, *, hwnd: int, process_id: int, point: tuple[int, int], **kwargs) -> bool: ...

    def click(self, *, hwnd: int, process_id: int, point: tuple[int, int], **kwargs) -> bool: ...

    def type_text(self, *, hwnd: int, process_id: int, point: tuple[int, int], text: str, **kwargs) -> bool:
        """Click ``point``, select all in the focused control, then type ``text`` (fill replaces).

        ``region`` is the fill target's rect; a delivery may refuse the keys when the click evidently
        focused nothing inside it, raising instead of returning false because the click was delivered.
        A delivery that reads the text caret sets ``last_input_focus`` when the click put it in an input.
        """

    def focus_is_valid(self, *, hwnd: int, process_id: int, foreground_hwnds: Sequence[int]) -> bool: ...

    def press_key(self, *, hwnd: int, process_id: int, key: str, foreground_hwnds: Sequence[int]) -> bool: ...

    def scroll(self, *, hwnd: int, process_id: int, point: tuple[int, int], notches: int, **kwargs) -> bool:
        """Wheel ``notches`` at ``point``; positive scrolls up (WHEEL_DELTA sign)."""

    def drag(
        self, *, hwnd: int, process_id: int, start: tuple[int, int], end: tuple[int, int], **kwargs
    ) -> bool:
        """Deliver one checked left-button drag; partial delivery must never return true."""


class ScreenGroundedAdapter:
    """A target-window visual path; no implicit fallback from UIA pattern failures."""

    ownership = "attached"

    def __init__(
        self,
        *,
        capture: FrameCapture,
        extractor: VisualCandidateExtractor,
        pointer: PointerDelivery,
        guard: SyntheticInputGuard,
        deadline_monotonic: float,
        indicator: DeliveryIndicator | None = None,
        operations: Sequence[str] = ("click",),
        uia_reader: Callable[[int], Sequence] | None = None,
        uia_scroller: Callable[..., None] | None = None,
        uia_range_setter: Callable[..., bool] | None = None,
        drop_captures: Sequence[FrameCapture] = (),
    ) -> None:
        unknown = set(operations) - set(SCREEN_OPERATIONS)
        if unknown:
            raise ValueError(f"unsupported screen operations: {sorted(unknown)}")
        self.operations = tuple(operations)
        self.capture = capture
        self.extractor = extractor
        self.pointer = pointer
        self.guard = guard
        self.deadline_monotonic = deadline_monotonic
        self.indicator = indicator or _NoOpIndicator()
        self._frames: dict[str, tuple[WindowFrame, ...]] = {}
        self._regions: dict[str, tuple[tuple[VisualRegion, ...], ...]] = {}
        # ADR-0036: candidates come from COM UIA where it answers; OCR stays the fallback and the evidence source.
        self.uia_reader = uia_reader
        self.uia_scroller = uia_scroller
        self.uia_range_setter = uia_range_setter
        self._uia_hwnds: dict[str, frozenset[int]] = {}
        self._ocr_candidates: set[str] = set()
        # Field values read with each UIA frame: after the action, only the observation still knows the before state.
        self._values: dict[str, tuple[tuple[VisualRegion, ...], ...]] = {}
        self._uia_semantic_ids: dict[str, str] = {}
        # (hwnd, direction, list rect) whose last wheel changed nothing: the list's end was reached.
        self._exhausted: set[tuple[int, str, Rect]] = set()
        self._pending_scroll: tuple[tuple[WindowFrame, ...], int, str, Rect] | None = None
        self._sleep = time.sleep
        # ADR-0045: windows named only as drag drop targets. They are captured when a drag end is chosen, never in
        # observe(), and offer end candidates only: no other operation, evidence or freshness reads them.
        self.drop_captures = tuple(drop_captures)
        self._drops: dict[str, _DropScope] = {}
        # Cumulative per-stage wall time; the coordinator diffs it per goal (plan uncertain-direct-pick step 1).
        self.stage_ms: dict[str, int] = {}
        self.indicator.show()

    def _extract_regions(self, frame: WindowFrame) -> tuple[VisualRegion, ...]:
        # BUG-0051: one OCR box outside the captured image must not abort the observation.
        bounded = []
        for region in self.extractor.extract(frame):
            left, top, width, height = region.rect
            clipped_left, clipped_top = max(left, 0), max(top, 0)
            right, bottom = min(left + width, frame.width), min(top + height, frame.height)
            if right <= clipped_left or bottom <= clipped_top:
                continue
            rect = (clipped_left, clipped_top, right - clipped_left, bottom - clipped_top)
            bounded.append(region if rect == tuple(region.rect) else replace(region, rect=rect))
        return tuple(bounded)

    def observe(self) -> Observation:
        with self._timed("observe_capture"):
            frames = self._capture_frames()
            if self._pending_scroll is not None:
                frames = self._settle_scroll(frames)
        root = frames[0]
        candidates: list[ObservedCandidate] = []
        semantic_parts: list[str] = []
        extracted: list[tuple[VisualRegion, ...]] = []
        values: list[tuple[VisualRegion, ...]] = []
        uia_hwnds: set[int] = set()
        uia_parts: list[str] = []
        ocr_fills: set[str] = set()
        for frame in frames:
            elements = self._read_uia(frame.hwnd)
            if elements:
                regions = self._uia_and_uncovered(frame, elements)
                uia_hwnds.add(frame.hwnd)
            else:
                with self._timed("observe_extract"):
                    regions = tuple(self._extract_regions(frame))
            extracted.append(regions)
            values.append(_uia_value_regions(elements or ()))
            popup = frame.hwnd != root.hwnd
            pointable = _pointable(regions)
            if "set_range" in self.operations and elements:
                candidates.extend(_uia_range_candidates(frame, elements, popup=popup))
                # A click or drag on a slider lands wherever its pixels put the thumb; the number is exact.
                pointable = tuple(region for region in pointable if region.evidence != "uia:slider")
            states = _uia_states(elements or ())
            for gesture in POINTER_OPERATIONS:
                if gesture in self.operations:
                    candidates.extend(
                        _with_state(_candidate(frame, region, popup=popup, operation=gesture), states)
                        for region in pointable
                    )
            if "fill" in self.operations and elements and any(e.editable for e in elements):
                candidates.extend(_uia_fill_candidates(frame, elements, popup=popup))
            elif "fill" in self.operations and elements:
                # Electron draws its editor without an editable element (ADR-0036 追記2), yet dropping UIA for OCR
                # lost the taskbar's pinned buttons (BUG-0040): fills come from the OCR only.
                with self._timed("observe_panels"):
                    fills = _fill_candidates(frame, _ocr_only(regions), popup=popup)
                candidates.extend(fills)
                ocr_fills.update(c.id for c in fills)
            elif "fill" in self.operations:
                with self._timed("observe_panels"):
                    candidates.extend(_fill_candidates(frame, regions, popup=popup))
            if "drag" in self.operations:
                candidates.extend(
                    _drag_start_candidate(frame, region, popup=popup)
                    for region in pointable
                    if not region.evidence.startswith(EDGE_CONTOUR_SOURCE)
                )
            if "scroll" in self.operations:
                candidates.extend(
                    candidate
                    for candidate in _scroll_candidates(frame, pointable, popup=popup)
                    if not self._is_exhausted(frame.hwnd, candidate)
                )
            semantic_parts.extend(_semantic_parts(frame, regions, popup=popup))
            semantic_parts.extend(_value_parts(frame, elements or (), popup=popup))
            semantic_parts.extend(_state_parts(frame, states, popup=popup))
            uia_parts.extend(_uia_semantic_parts(frame, regions, elements, popup=popup))
        if "key" in self.operations:
            # FILL selects all itself; offered beside it, the provider still chose Ctrl+A first and then
            # doubted the selection it had made (plan screen-input-ops, VSCode comparison run).
            keys = [key for key in KEY_ALLOWLIST if not (key == "Ctrl+A" and "fill" in self.operations)]
            candidates.extend(_key_candidate(root, key) for key in keys)
        observation_id = _observation_id(frames)
        semantic_id = _digest(semantic_parts)
        self._frames[observation_id] = frames
        self._regions[observation_id] = tuple(extracted)
        self._uia_hwnds[observation_id] = frozenset(uia_hwnds)
        self._ocr_candidates.update(
            c.id for c in candidates if (_scope_hwnd(c) or root.hwnd) not in uia_hwnds or c.id in ocr_fills
        )
        self._values[observation_id] = tuple(values)
        self._uia_semantic_ids[observation_id] = _digest(uia_parts)
        # A captioned field's own text is its current value, apart from the caption naming it (BUG-0034):
        # read inside one label, the goal's "Name" matched a list of *name rows as well as "Name: OLDVALUE".
        values = {c.id: c.binding["field_label"] for c in candidates if "field_label" in c.binding}
        return Observation(observation_id, semantic_id, tuple(candidates), True, untrusted_values=values)

    def screen_evidence(self, observation: Observation, candidate: ObservedCandidate) -> ScreenEvidence | None:
        """The frame holding ``candidate``'s scope in ``observation``, or the root's if that popup already closed.

        BUG-0023: selecting a popup item (e.g. a dropdown option) closes the popup itself, so the
        post-click observation never re-captures its hwnd. The selection's effect shows in the root
        window instead, so achievement E falls back to reading that rather than losing evidence to
        ``None`` (``landed_conn``'s own checks tell a genuine miss from this fallback by comparing
        ``before.frame.hwnd`` and ``after.frame.hwnd``).
        """

        frames = self._frames_for(observation)
        hwnd = _scope_hwnd(candidate) or frames[0].hwnd
        if not any(frame.hwnd == hwnd for frame in frames):
            hwnd = frames[0].hwnd
        return self._evidence_for_hwnd(observation, hwnd, candidate.id)

    def scroll_progress(self, before: Observation, after: Observation, candidate: ObservedCandidate) -> bool:
        """The scroll moved the rows of its own area and brought new ones into view (EXP-0013)."""

        from .achievement import scroll_shift

        old, new = self.screen_evidence(before, candidate), self.screen_evidence(after, candidate)
        if candidate.operation != "scroll" or old is None or new is None or old.frame.hwnd != new.frame.hwnd:
            return False
        shift = scroll_shift(old, new, list(candidate.attributes["rect"]), candidate.attributes["direction"])
        # A still list whose edge row reads differently each time shows a "new" row without moving (EXP-0008).
        return shift["moved_rows"] >= 2 and bool(shift["revealed"])

    def root_evidence(self, observation: Observation) -> ScreenEvidence | None:
        """The root window's own evidence in ``observation``, regardless of any candidate's popup scope."""

        frames = self._frames_for(observation)
        return self._evidence_for_hwnd(observation, frames[0].hwnd) if frames else None

    def _evidence_for_hwnd(
        self, observation: Observation, hwnd: int, candidate_id: str | None = None
    ) -> ScreenEvidence | None:
        frames = self._frames_for(observation)
        regions_by_frame = self._regions.get(observation.observation_id)
        if regions_by_frame is None:
            return None
        index = next((i for i, frame in enumerate(frames) if frame.hwnd == hwnd), None)
        if index is None:
            return None
        frame, regions = frames[index], regions_by_frame[index]
        uia_frame = frame.hwnd in self._uia_hwnds.get(observation.observation_id, ())
        if uia_frame and candidate_id in self._ocr_candidates:
            # Before and after must come from one source: a VSCode fill offered from OCR opened an editable quick
            # input, so the after read switched to UIA and every label looked new, pushing the value out (ADR-0036 追記2).
            regions = tuple(self._extract_regions(frame))
            fills = _fill_candidates(frame, regions, popup=False)
        elif uia_frame:
            # ADR-0036 追記: UIA names plus field values replace the full OCR pass (about 2.6s per verdict in E2E-03).
            # The value regions keep a fill's landing visible when the field's name stays the same (consult
            # 20260925-2134 point 5). Panels and captions are OCR layout guesses, read from the uncovered OCR only.
            values = self._values.get(observation.observation_id, ())
            # Whether covered text was read depends on an editable element existing, which a fill itself can change.
            regions = tuple(r for r in _pointable(regions) if not r.evidence.endswith(":offscreen"))
            fills = _fill_candidates(frame, _ocr_only(regions), popup=False)
            regions += values[index] if index < len(values) else ()
        else:
            fills = _fill_candidates(frame, regions, popup=False)
        return ScreenEvidence(
            frame=frame,
            regions=regions,
            panels=tuple(
                (c.attributes["rect"], tuple(m[1] for m in c.binding["members"]))
                for c in fills
                if c.attributes.get("source") == FILL_PANEL_SOURCE
            ),
            captions=tuple(
                (c.binding["caption"][1], c.binding["field_label"]) for c in fills if "caption" in c.binding
            ),
            row_stacks=tuple(sorted({c.attributes["rect"] for c in _scroll_candidates(frame, regions, popup=False)})),
        )

    def fresh(self, observation: Observation, candidate: ObservedCandidate | None = None) -> Freshness:
        if candidate is not None and candidate.attributes.get("scope") == DROP_SCOPE:
            # ADR-0045: the drop window is judged by identity here and by its endpoint at delivery (_drag), so an
            # unrelated repaint there does not force endless re-observation; the start window is judged as usual.
            drop = self._drops.get(observation.observation_id)
            if drop is None:
                raise ScopeViolation("drop observation is not retained")
            current = self._capture_drops(self._frames_for(observation))
            if len(current) != len(drop.frames) or not all(map(_same_scope, drop.frames, current)):
                return Freshness.STALE
            return self.fresh(observation, None)
        original = self._frames_for(observation)
        with self._timed("fresh_capture"):
            current = self._capture_frames()
        if not _same_scope(original[0], current[0]):
            raise ScopeViolation("captured window identity changed")
        scope_hwnd = _scope_hwnd(candidate) if candidate is not None else None
        uia_hwnds = self._uia_hwnds.get(observation.observation_id, frozenset())
        if uia_hwnds and (scope_hwnd is None or scope_hwnd in uia_hwnds):
            # Same pixels do not mean the same UIA state, so UIA is read again (consult 20260925-2134 point 3).
            return Freshness.FRESH if self._same_meaning(observation, current, candidate) else Freshness.STALE
        if scope_hwnd is None:
            if _observation_id(current) == observation.observation_id or _equivalent_frames(original, current):
                return Freshness.FRESH
            return Freshness.FRESH if self._same_meaning(observation, current, candidate) else Freshness.STALE
        # ADR-0012 A: judge only the selected candidate's own window; an unrelated repaint of the
        # root while a popup is open must not force endless re-observation.
        before = _frame_by_hwnd(original, scope_hwnd)
        after = _frame_by_hwnd(current, scope_hwnd)
        if before is None or after is None or not _same_scope(before, after):
            return Freshness.STALE
        return Freshness.FRESH if _equivalent_frames((before,), (after,)) else Freshness.STALE

    def act(
        self, candidate: ObservedCandidate, observation: Observation, text: str | None = None
    ) -> MutationResult:
        operation = candidate.operation
        method = "synthetic_pointer" if operation in (*POINTER_OPERATIONS, "scroll", "drag") else "synthetic_key"
        if operation not in self.operations:
            return MutationResult(candidate.id, method, "not_attempted", f"operation not offered: {operation}")
        if (text is not None) != (operation in ("fill", "set_range")):
            return MutationResult(candidate.id, method, "not_attempted", "text is required by fill and only by fill")
        if operation == "set_range":
            return self._set_range(candidate, observation, text)
        if text is not None and any(ord(char) < 32 and char not in "\n\t" for char in text.replace("\r\n", "\n")):
            return MutationResult(candidate.id, method, "not_attempted", "text contains control characters")
        if operation == "key":
            return self._press_key(candidate, observation)
        if operation == "scroll":
            return self._scroll(candidate, observation)
        if operation == "drag":
            return self._drag(candidate, observation)
        frames = self._frames_for(observation)
        root = frames[0]
        scope_hwnd = _scope_hwnd(candidate)
        original = root if scope_hwnd is None else _frame_by_hwnd(frames, scope_hwnd)
        if original is None:
            return MutationResult(
                candidate.id, "synthetic_pointer", "not_attempted", "candidate window is not in scope"
            )
        pointer_scope = {} if scope_hwnd is None else {"foreground_hwnds": (root.hwnd, original.hwnd)}
        reticle_stack = ExitStack()
        input_focus = []

        def resolve_target(_observation, wanted):
            with self._timed("resolve_capture"):
                current_frames = self._capture_frames()
            if not _same_scope(root, current_frames[0]):
                raise ScopeViolation("captured window identity changed")
            current = _frame_by_hwnd(current_frames, original.hwnd)
            if current is None or not _same_scope(original, current):
                return None
            rect = _candidate_rect(wanted)
            uia = current.hwnd in self._uia_hwnds.get(observation.observation_id, ())
            # Identical pixels can only reproduce the extraction that produced the candidate, so the
            # full re-OCR (about 2s on Unity, docs/plans/uncertain-direct-pick.md) is skipped. UIA state is read
            # again even then, just before the input (consult 20260925-2134 point 3).
            equivalent = not uia and _equivalent_frames((original,), (current,))
            if uia and "offscreen" in wanted.attributes:
                role = wanted.binding.get("evidence", "").split(":")[1]
                if self.uia_scroller is None:
                    return None
                try:
                    self.uia_scroller(current.hwnd, role, wanted.label)
                except Exception:  # noqa: BLE001 - not scrolled into view means not delivered
                    return None
                # The scroll moved it, so it is found again by role and name, now on screen, exactly once.
                current = self._capture_frames()[0]
                regions = self._regions_now(current, {current.hwnd}) or ()
                moved = [r for r in regions if r.label == wanted.label and r.evidence == f"uia:{role}"]
                if len(moved) != 1:
                    return None
                rect = _checked_rect(moved[0].rect, current.width, current.height)
                wanted = replace(wanted, attributes={**wanted.attributes, "rect": rect})
            elif uia:
                if _uia_evidence(wanted) and not (_is_fill_panel(wanted) or "caption" in wanted.binding):
                    # Re-identification below matches this UIA region only. OCR of uncovered pixels cannot
                    # change that match, and remains available to OCR candidates and full observations.
                    elements = self._read_uia(current.hwnd)
                    regions = _uia_regions(elements) if elements else ()
                else:
                    # A blinking caret flips OCR of its whole line; the observed reading stands (see _read_now).
                    regions = self._read_now(current, {current.hwnd}, self._observed(observation, original.hwnd))[0] or ()
                if _is_fill_panel(wanted) or "caption" in wanted.binding:
                    if not _fill_still_there(current, _ocr_only(regions), wanted, popup=scope_hwnd is not None):
                        return None
                elif sum(
                    region.id == wanted.subject
                    and region.label == wanted.label
                    and region.rect == rect
                    and region.evidence == wanted.binding.get("evidence")
                    for region in regions
                ) != 1:
                    return None
            elif not equivalent:
                with self._timed("resolve_extract"):
                    regions = tuple(self._extract_regions(current))
                if _is_fill_panel(wanted) or "caption" in wanted.binding:
                    if not _fill_still_there(current, regions, wanted, popup=scope_hwnd is not None):
                        return None
                elif sum(
                    region.id == wanted.subject
                    and region.label == wanted.label
                    and region.rect == rect
                    and region.evidence == wanted.binding.get("evidence")
                    for region in regions
                ) != 1:
                    return None
            # A UIA candidate is re-identified by UIA, not by pixels a hover highlight changes; OCR ones by pixels.
            if (
                not _uia_evidence(wanted)
                and not equivalent
                and operation != "fill"
                and current.anchor_fingerprint(rect) != wanted.binding.get("anchor_fingerprint")
            ):
                return None
            # ADR-0010: reticle appears once the target is confirmed and stays up through send().
            reticle_stack.enter_context(
                self.indicator.reticle(hwnd=original.hwnd, rect=rect, action=_reticle_action(operation))
            )
            return wanted

        def verify_delivery_target(wanted, _method):
            return self._delivery_verdict(
                hwnd=original.hwnd, process_id=original.process_id, point=_delivery_point(wanted), **pointer_scope
            )

        def send(wanted, _method):
            point = _delivery_point(wanted)
            with self._timed("send"):
                if text is not None:
                    typed = self.pointer.type_text(
                        hwnd=original.hwnd,
                        process_id=original.process_id,
                        point=point,
                        text=text.replace("\r\n", "\n"),
                        region=_candidate_rect(wanted),
                        **pointer_scope,
                    )
                    input_focus.append(getattr(self.pointer, "last_input_focus", False))
                    return typed
                return self.pointer.click(
                    hwnd=original.hwnd,
                    process_id=original.process_id,
                    point=point,
                    **({} if operation == "click" else {"gesture": operation}),
                    **pointer_scope,
                )

        try:
            result = self.guard.execute(
                candidate=candidate,
                observation=observation,
                input_method=method,
                deadline_monotonic=self.deadline_monotonic,
                resolve_target=resolve_target,
                verify_delivery_target=verify_delivery_target,
                send=send,
            )
            return replace(result, input_focus=True) if result.status == "confirmed" and any(input_focus) else result
        finally:
            reticle_stack.close()

    def _set_range(self, candidate: ObservedCandidate, observation: Observation, text: str) -> MutationResult:
        low, high = candidate.binding.get("range", (None, None))
        try:
            number = float(text.strip())
        except ValueError:
            return MutationResult(candidate.id, "pattern", "not_attempted", "set_range requires a number")
        if low is None or not low <= number <= high:
            return MutationResult(candidate.id, "pattern", "not_attempted", f"set_range value outside {low}..{high}")
        if self.uia_range_setter is None:
            return MutationResult(candidate.id, "pattern", "not_attempted", "UIA RangeValue is unavailable")
        frames = self._frames_for(observation)
        original = _frame_by_hwnd(frames, _scope_hwnd(candidate) or frames[0].hwnd)
        if original is None:
            return MutationResult(candidate.id, "pattern", "not_attempted", "candidate window is not in scope")

        def resolve_target(_observation, wanted):
            current_frames = self._capture_frames()
            if not _same_scope(frames[0], current_frames[0]):
                raise ScopeViolation("captured window identity changed")
            current = _frame_by_hwnd(current_frames, original.hwnd)
            if current is None or not _same_scope(original, current):
                return None
            elements = self._read_uia(current.hwnd) or ()
            same = [e for e in elements if e.role == "slider" and _uia_region(e).id == wanted.subject]
            return wanted if len(same) == 1 else None

        def send(wanted, _method):
            with self._timed("send"):
                return self.uia_range_setter(original.hwnd, wanted.label, number)

        return self.guard.execute(
            candidate=candidate,
            observation=observation,
            input_method="pattern",
            deadline_monotonic=self.deadline_monotonic,
            resolve_target=resolve_target,
            verify_delivery_target=lambda _wanted, _method: True,
            send=send,
        )

    def drag_end_observation(
        self, start: ObservedCandidate, observation: Observation
    ) -> Observation:
        """Turn a selected non-mutating start into one composite candidate per observed end."""

        if (
            "drag" not in self.operations
            or start.operation != "drag"
            or start.attributes.get("drag_phase") != "start"
        ):
            raise ScopeViolation("candidate is not an offered drag start")
        frames = self._frames_for(observation)
        root = frames[0]
        start_hwnd = _scope_hwnd(start) or int(start.binding.get("frame_hwnd", root.hwnd))
        observed = self._regions.get(observation.observation_id)
        if observed is None:
            raise ScopeViolation("drag start observation is not retained")
        candidates: list[ObservedCandidate] = []
        semantic_parts: list[str] = []
        # Ends come from the sources the start was offered from (ADR-0044), so a UIA end is a UIA region.
        for frame, regions in zip(frames, observed):
            popup = frame.hwnd != root.hwnd
            if frame.hwnd == start_hwnd:
                candidates.extend(
                    _drag_end_candidate(
                        start, frame, region, popup=popup, base_observation_id=observation.observation_id
                    )
                    for region in _pointable(regions)
                )
            semantic_parts.extend(_semantic_parts(frame, regions, popup=popup))
        identity = f"{observation.observation_id}\0drag-end\0{start.id}"
        observation_id = hashlib.sha256(identity.encode()).hexdigest()
        self._frames[observation_id] = frames
        if self.drop_captures:
            drop = self._observe_drops(frames)
            self._drops[observation_id] = drop
            for frame, regions in zip(drop.frames, drop.regions):
                candidates.extend(
                    _drag_end_candidate(
                        start, frame, region, popup=True, base_observation_id=observation.observation_id, drop=True
                    )
                    for region in _pointable(regions)
                )
                semantic_parts.extend(_semantic_parts(frame, regions, popup=True))
        return Observation(
            observation_id,
            hashlib.sha256("\0".join(semantic_parts + [f"drag-start:{start.id}"]).encode()).hexdigest(),
            tuple(candidates),
            True,
        )

    def _capture_drops(self, scope_frames: Sequence[WindowFrame]) -> tuple[WindowFrame, ...]:
        frames = tuple(capture.capture() for capture in self.drop_captures)
        hwnds = [frame.hwnd for frame in (*scope_frames, *frames)]
        if len(set(hwnds)) != len(hwnds):
            raise ScopeViolation("drop window is part of the run's own scope")
        return frames

    def _observe_drops(self, scope_frames: Sequence[WindowFrame]) -> "_DropScope":
        frames = self._capture_drops(scope_frames)
        regions: list[tuple[VisualRegion, ...]] = []
        uia_hwnds: set[int] = set()
        for frame in frames:
            elements = self._read_uia(frame.hwnd)
            if elements:
                regions.append(self._uia_and_uncovered(frame, elements))
                uia_hwnds.add(frame.hwnd)
            else:
                with self._timed("observe_extract"):
                    regions.append(tuple(self._extract_regions(frame)))
        return _DropScope(frames, tuple(regions), frozenset(uia_hwnds))

    def _drag(self, candidate: ObservedCandidate, observation: Observation) -> MutationResult:
        def refuse(detail: str) -> MutationResult:
            return MutationResult(candidate.id, "synthetic_pointer", "not_attempted", detail)

        if candidate.attributes.get("drag_phase") != "end":
            return refuse("drag end is not selected")
        frames = self._frames_for(observation)
        root = frames[0]
        start_hwnd = candidate.binding.get("start_hwnd")
        end_hwnd = _scope_hwnd(candidate) or root.hwnd
        if not isinstance(start_hwnd, int):
            return refuse("invalid drag start")
        to_drop = candidate.attributes.get("scope") == DROP_SCOPE
        drop = self._drops.get(observation.observation_id)
        if to_drop:
            end_original = _frame_by_hwnd(drop.frames, end_hwnd) if drop is not None else None
            if end_original is None:
                return refuse("drop window is not retained")
        elif start_hwnd != end_hwnd:
            return refuse("drag endpoints cross windows")
        original = _frame_by_hwnd(frames, start_hwnd)
        if original is None:
            return refuse("candidate window is not in scope")
        end_original = end_original if to_drop else original
        start_rect = candidate.binding.get("start_rect")
        end_rect = _candidate_rect(candidate)
        if not _valid_rect_value(start_rect):
            return refuse("invalid drag start")
        pointer_scope = {} if start_hwnd == root.hwnd else {"foreground_hwnds": (root.hwnd, start_hwnd)}
        uia_hwnds = self._uia_hwnds.get(candidate.binding.get("base_observation_id"), frozenset())
        drop_uia = drop.uia_hwnds if drop is not None else frozenset()
        reticle_stack = ExitStack()

        def still_there(current: WindowFrame, before: WindowFrame, uia, pairs) -> bool:
            if _equivalent_frames((before,), (current,)):
                return True
            regions = self._regions_now(current, uia)
            return regions is not None and all(
                sum(
                    region.id == subject and region.label == label and region.rect == rect and region.evidence == evidence
                    for region in regions
                )
                == 1
                for subject, label, rect, evidence in pairs
            )

        start_pair = (
            candidate.binding.get("start_subject"), candidate.binding.get("start_label"), start_rect,
            candidate.binding.get("start_evidence"),
        )
        end_pair = (candidate.subject, candidate.binding.get("end_label"), end_rect, candidate.binding.get("evidence"))

        def resolve_target(_observation, wanted):
            with self._timed("resolve_capture"):
                current_frames = self._capture_frames()
                current_drops = self._capture_drops(current_frames) if to_drop else ()
            if not _same_scope(root, current_frames[0]):
                raise ScopeViolation("captured window identity changed")
            current = _frame_by_hwnd(current_frames, start_hwnd)
            if current is None or not _same_scope(original, current):
                return None
            with self._timed("resolve_extract"):
                if to_drop:
                    # ADR-0045: each endpoint is re-identified in its own window; a drop window that moved or
                    # changed identity refuses before any button goes down.
                    current_end = _frame_by_hwnd(current_drops, end_hwnd)
                    if current_end is None or not _same_scope(end_original, current_end):
                        return None
                    if not still_there(current, original, uia_hwnds, (start_pair,)):
                        return None
                    if not still_there(current_end, end_original, drop_uia, (end_pair,)):
                        return None
                elif not still_there(current, original, uia_hwnds, (start_pair, end_pair)):
                    return None
            reticle_stack.enter_context(
                self.indicator.reticle(
                    hwnd=start_hwnd,
                    rect=start_rect if to_drop else _bounding_rect((start_rect, end_rect)),
                    action="select",
                )
            )
            return wanted

        def verify_delivery_target(_wanted, _method):
            if not self.pointer.target_is_valid(
                hwnd=start_hwnd, process_id=original.process_id, point=_center(start_rect), **pointer_scope
            ):
                return False
            return self.pointer.target_is_valid(
                hwnd=end_hwnd, process_id=end_original.process_id, point=_center(end_rect),
                **({"foreground_hwnds": (end_hwnd,)} if to_drop else pointer_scope),
            )

        def send(_wanted, _method):
            with self._timed("send"):
                return self.pointer.drag(
                    hwnd=start_hwnd,
                    process_id=original.process_id,
                    start=_center(start_rect),
                    end=_center(end_rect),
                    **pointer_scope,
                    **({"end_hwnd": end_hwnd, "end_process_id": end_original.process_id} if to_drop else {}),
                )

        try:
            return self.guard.execute(
                candidate=candidate,
                observation=observation,
                input_method="synthetic_pointer",
                deadline_monotonic=self.deadline_monotonic,
                resolve_target=resolve_target,
                verify_delivery_target=verify_delivery_target,
                send=send,
            )
        finally:
            reticle_stack.close()

    def _press_key(self, candidate: ObservedCandidate, observation: Observation) -> MutationResult:
        root = self._frames_for(observation)[0]
        key = candidate.attributes.get("key")
        if key not in KEY_ALLOWLIST:
            return MutationResult(candidate.id, "synthetic_key", "not_attempted", "key is not allowlisted")
        in_scope: list[int] = []
        reticle_stack = ExitStack()

        def resolve_target(_observation, wanted):
            with self._timed("resolve_capture"):
                current = self._capture_frames()
            if not _same_scope(root, current[0]):
                raise ScopeViolation("captured window identity changed")
            # A key has no anchor to re-identify, so everything the decision saw must still read the same.
            if not self._reads_same(observation, current):
                return None
            # Keys land on the foreground window, which may be an owned popup such as an open menu.
            in_scope[:] = [frame.hwnd for frame in current]
            return wanted

        def verify_delivery_target(_wanted, _method):
            scope = {"hwnd": root.hwnd, "process_id": root.process_id, "foreground_hwnds": tuple(in_scope)}
            focus_target = getattr(self.pointer, "focus_target", None)
            if focus_target is None:
                valid, focus = self.pointer.focus_is_valid(**scope), None
            else:
                valid, focus = focus_target(**scope)
            # E2E-I7: frame where the key lands; the whole window was the full screen for a desktop target.
            rect = (_clipped(focus, root) if focus is not None else None) or (0, 0, root.width, root.height)
            reticle_stack.enter_context(self.indicator.reticle(hwnd=root.hwnd, rect=rect, action="type"))
            return valid

        def send(_wanted, _method):
            with self._timed("send"):
                return self.pointer.press_key(
                    hwnd=root.hwnd, process_id=root.process_id, key=key, foreground_hwnds=tuple(in_scope)
                )

        try:
            return self.guard.execute(
                candidate=candidate,
                observation=observation,
                input_method="synthetic_key",
                deadline_monotonic=self.deadline_monotonic,
                resolve_target=resolve_target,
                verify_delivery_target=verify_delivery_target,
                send=send,
            )
        finally:
            reticle_stack.close()

    def _scroll(self, candidate: ObservedCandidate, observation: Observation) -> MutationResult:
        frames = self._frames_for(observation)
        root = frames[0]
        scope_hwnd = _scope_hwnd(candidate)
        original = root if scope_hwnd is None else _frame_by_hwnd(frames, scope_hwnd)
        direction = candidate.attributes.get("direction")
        notches = candidate.attributes.get("notches")
        if original is None or direction not in ("up", "down") or not isinstance(notches, int) or notches < 1:
            return MutationResult(candidate.id, "synthetic_pointer", "not_attempted", "invalid scroll candidate")
        rect = _candidate_rect(candidate)
        point = _center(rect)
        pointer_scope = {} if scope_hwnd is None else {"foreground_hwnds": (root.hwnd, original.hwnd)}
        sent_against: list[tuple[WindowFrame, ...]] = []
        reticle_stack = ExitStack()

        def resolve_target(_observation, wanted):
            with self._timed("resolve_capture"):
                current = self._capture_frames()
            if not _same_scope(root, current[0]):
                raise ScopeViolation("captured window identity changed")
            # The wheel lands on whatever list is under the point, so the whole scope must still read the same.
            if not self._reads_same(observation, current):
                return None
            sent_against[:] = [current]
            reticle_stack.enter_context(self.indicator.reticle(hwnd=original.hwnd, rect=rect, action="select"))
            return wanted

        def verify_delivery_target(_wanted, _method):
            return self._delivery_verdict(
                hwnd=original.hwnd, process_id=original.process_id, point=point, **pointer_scope
            )

        def send(_wanted, _method):
            with self._timed("send"):
                return self.pointer.scroll(
                    hwnd=original.hwnd,
                    process_id=original.process_id,
                    point=point,
                    notches=notches if direction == "up" else -notches,
                    **pointer_scope,
                )

        try:
            result = self.guard.execute(
                candidate=candidate,
                observation=observation,
                input_method="synthetic_pointer",
                deadline_monotonic=self.deadline_monotonic,
                resolve_target=resolve_target,
                verify_delivery_target=verify_delivery_target,
                send=send,
            )
        finally:
            reticle_stack.close()
        if result.status == "confirmed" and sent_against:
            self._pending_scroll = (sent_against[0], original.hwnd, direction, rect)
        return result

    def _settle_scroll(self, frames: tuple[WindowFrame, ...]) -> tuple[WindowFrame, ...]:
        before, hwnd, direction, rect = self._pending_scroll
        self._pending_scroll = None
        if _equivalent_frames(before, frames):
            self._sleep(SCROLL_SETTLE_SECONDS)
            frames = self._capture_frames()
        point = _center(rect)
        if _equivalent_frames(before, frames):
            self._exhausted.add((hwnd, direction, rect))
        else:
            # Moving the list reopens both of its ends.
            self._exhausted = {
                entry for entry in self._exhausted if entry[0] != hwnd or not _contains(entry[2], point)
            }
        return frames

    def _is_exhausted(self, hwnd: int, candidate: ObservedCandidate) -> bool:
        center = _center(_candidate_rect(candidate))
        direction = candidate.attributes.get("direction")
        return any(
            entry_hwnd == hwnd and entry_direction == direction and _contains(rect, center)
            for entry_hwnd, entry_direction, rect in self._exhausted
        )

    def _same_meaning(
        self, observation: Observation, current: Sequence[WindowFrame], candidate: ObservedCandidate | None
    ) -> bool:
        """Pixels changed but the finite regions did not, e.g. a blinking caret (VSCode, live)."""

        if len(current) != len(self._frames_for(observation)):
            return False
        uia_hwnds = self._uia_hwnds.get(observation.observation_id, frozenset())
        # A fill target is an editable field whose own caret blinks, so it is re-identified by its region only.
        # A UIA candidate is re-identified by UIA, not by pixels a hover highlight changes.
        uia_source = candidate is not None and bool(uia_hwnds) and _uia_evidence(candidate)
        if candidate is not None and candidate.operation == "click" and not uia_source:
            own = _frame_by_hwnd(current, _scope_hwnd(candidate) or current[0].hwnd)
            if own is None or own.anchor_fingerprint(_candidate_rect(candidate)) != candidate.binding.get("anchor_fingerprint"):
                return False
        parts: list[str] = []
        uia_parts: list[str] = []
        panel_seen = candidate is None or not _is_fill_panel(candidate)
        original = self._frames_for(observation)
        observed = self._regions.get(observation.observation_id, ())
        with self._timed("fresh_extract"):
            for index, frame in enumerate(current):
                if uia_source and frame.hwnd in uia_hwnds:
                    # The UIA semantic id deliberately excludes uncovered OCR. Re-reading that OCR here
                    # cannot affect this candidate's freshness, but can take hundreds of ms per repaint.
                    elements = self._read_uia(frame.hwnd)
                    if not elements:
                        return False
                    regions = _uia_regions(elements)
                else:
                    before = (original[index], observed[index]) if index < len(observed) else None
                    regions, elements = self._read_now(frame, uia_hwnds, before)
                    if regions is None:
                        return False
                popup = frame.hwnd != current[0].hwnd
                parts.extend(_semantic_parts(frame, regions, popup=popup))
                parts.extend(_value_parts(frame, elements, popup=popup))
                parts.extend(_state_parts(frame, _uia_states(elements or ()), popup=popup))
                uia_parts.extend(_uia_semantic_parts(frame, regions, elements if frame.hwnd in uia_hwnds else None, popup=popup))
                if not panel_seen and frame.hwnd == (_scope_hwnd(candidate) or current[0].hwnd):
                    # Unchanged text can sit on a panel that lost its delivery point (consult 20260925-0949 point 4).
                    fillable = _ocr_only(regions) if frame.hwnd in uia_hwnds else regions
                    panel_seen = _fill_still_there(frame, fillable, candidate, popup=popup)
        if not panel_seen:
            return False
        uia_semantic_id = self._uia_semantic_ids.get(observation.observation_id)
        if uia_source and uia_semantic_id is not None:
            # E2E-I46: any repaint re-reads the uncovered OCR, whose noise made a still-present UIA popup item STALE.
            return _digest(uia_parts) == uia_semantic_id
        return _digest(parts) == observation.semantic_id

    def _reads_same(self, observation: Observation, current: Sequence[WindowFrame]) -> bool:
        if not self._uia_hwnds.get(observation.observation_id) and _equivalent_frames(
            self._frames_for(observation), current
        ):
            return True
        return self._same_meaning(observation, current, None)

    def _read_uia(self, hwnd: int) -> Sequence | None:
        if self.uia_reader is None:
            return None
        with self._timed("observe_uia"):
            try:
                return tuple(self.uia_reader(hwnd))
            except Exception:  # noqa: BLE001 - no UIA answer before input means OCR, never a guess
                return None

    def _observed(self, observation: Observation, hwnd: int) -> tuple[WindowFrame, tuple[VisualRegion, ...]] | None:
        frames = self._frames_for(observation)
        observed = self._regions.get(observation.observation_id, ())
        index = next((i for i, frame in enumerate(frames) if frame.hwnd == hwnd), None)
        return None if index is None or index >= len(observed) else (frames[index], observed[index])

    def _regions_now(self, frame: WindowFrame, uia_hwnds) -> tuple[VisualRegion, ...] | None:
        """The frame's regions from the same sources the observation used; None when UIA no longer answers."""

        return self._read_now(frame, uia_hwnds)[0]

    def _read_now(
        self, frame: WindowFrame, uia_hwnds, before: tuple[WindowFrame, tuple[VisualRegion, ...]] | None = None
    ) -> tuple[tuple[VisualRegion, ...] | None, Sequence]:
        if frame.hwnd not in uia_hwnds:
            return tuple(self._extract_regions(frame)), ()
        elements = self._read_uia(frame.hwnd)
        if not elements:
            return None, ()
        if before is not None and before[0].hwnd == frame.hwnd and _equivalent_frames((before[0],), (frame,)):
            # A blinking caret flips OCR of its whole line (see _equivalent_frames); the observed reading stands.
            return _uia_regions(elements) + tuple(r for r in before[1] if not _is_uia(r)), elements
        return self._uia_and_uncovered(frame, elements), elements

    def _uia_and_uncovered(self, frame: WindowFrame, elements: Sequence) -> tuple[VisualRegion, ...]:
        """ADR-0044: UIA elements plus OCR of what named on-screen elements leave uncovered.

        Fills taken from OCR read the whole frame: VSCode's title-bar Search is drawn under a UIA button
        (BUG-0040). The text under UIA elements is then kept, marked covered, for fills only.
        """

        regions = _uia_regions(elements)
        whole = "fill" in self.operations and not any(e.editable for e in elements)
        if whole:
            with self._timed("observe_extract"):
                found, dx, dy = self._extract_regions(frame), 0, 0
        else:
            covered = [e.rect for e in elements if e.name and not getattr(e, "offscreen", False)]
            crop = uncovered_crop(frame, covered)
            if crop is None:
                return regions
            with self._timed("region_ocr"):
                found = self._extract_regions(crop.frame)
            dx, dy = crop.offset
        shown = [r.rect for r in regions if not r.evidence.endswith(":offscreen")]
        extra = []
        for region in found:
            rect = (region.rect[0] + dx, region.rect[1] + dy, region.rect[2], region.rect[3])
            # UIA names the same control more reliably than OCR of its pixels.
            if not any(_contains(uia, _center(rect)) for uia in shown):
                extra.append(replace(region, rect=rect))
            elif whole:
                extra.append(replace(region, rect=rect, evidence=region.evidence + COVERED_SUFFIX))
        return regions + tuple(extra)

    def effect(self, candidate: ObservedCandidate, before: Observation, after: Observation, text: str | None):
        """ADR-0039 on a UIA toggle or selection item; None for every other candidate."""

        state = candidate.attributes.get("checked")
        if candidate.operation != "click" or state is None:
            return None
        later = next((item for item in after.candidates if item.id == candidate.id), None)
        observed = later.attributes.get("checked") if later is not None else None
        return checked_effect(candidate.label, candidate.binding.get("selects", False), state, observed)

    def unchanged(self, before: Observation, after: Observation) -> bool:
        """Whether ``after`` shows the same pixels as ``before`` up to one text caret (E2E-I8).

        Pixels rather than the OCR semantic id, so a change without text (a toggle's colour) still counts;
        a clock or spinner makes this answer False, which only forgoes the no-effect stop.
        """

        before_frames, after_frames = self._frames.get(before.observation_id), self._frames.get(after.observation_id)
        if before_frames is None or after_frames is None:
            return False
        return before.observation_id == after.observation_id or _equivalent_frames(before_frames, after_frames)

    def retain(self, observation: Observation) -> RetainedScreen:
        observation_id = observation.observation_id
        ids = {candidate.id for candidate in observation.candidates}
        return RetainedScreen(
            frames=self._frames_for(observation),
            regions=self._regions.get(observation_id),
            uia_hwnds=self._uia_hwnds.get(observation_id, frozenset()),
            values=self._values.get(observation_id, ()),
            ocr_candidates=frozenset(ids & self._ocr_candidates),
            uia_semantic_id=self._uia_semantic_ids.get(observation_id),
        )

    def adopt(self, observation: Observation, retained: RetainedScreen) -> None:
        """Accept another run's observation for a pick (ADR-0022); fresh() and act() still re-capture it."""
        frames = tuple(retained.frames)
        base_ids = {candidate.binding.get("base_observation_id") for candidate in observation.candidates}
        if not frames or (
            _observation_id(frames) != observation.observation_id
            and base_ids != {_observation_id(frames)}
        ):
            raise ScopeViolation("retained frames do not match the observation")
        observation_id = observation.observation_id
        self._frames[observation_id] = frames
        if retained.regions is not None:
            self._regions[observation_id] = retained.regions
        self._uia_hwnds[observation_id] = retained.uia_hwnds
        self._values[observation_id] = retained.values
        self._ocr_candidates.update(retained.ocr_candidates)
        if retained.uia_semantic_id is not None:
            self._uia_semantic_ids[observation_id] = retained.uia_semantic_id

    def close(self) -> None:
        try:
            self.guard.close()
        finally:
            self.indicator.close()

    def _delivery_verdict(self, **kwargs) -> bool | str:
        if self.pointer.target_is_valid(**kwargs):
            return True
        return getattr(self.pointer, "last_refusal", None) or False

    @contextmanager
    def _timed(self, stage: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = round((time.perf_counter() - started) * 1000)
            self.stage_ms[stage] = self.stage_ms.get(stage, 0) + elapsed

    def _capture_frames(self) -> tuple[WindowFrame, ...]:
        capture_scope = getattr(self.capture, "capture_scope", None)
        frames = tuple(capture_scope()) if capture_scope is not None else (self.capture.capture(),)
        if not frames:
            raise ScopeViolation("scope capture returned no root frame")
        if len({frame.hwnd for frame in frames}) != len(frames):
            raise ScopeViolation("scope capture returned duplicate windows")
        return frames

    def _frames_for(self, observation: Observation) -> tuple[WindowFrame, ...]:
        frames = self._frames.get(observation.observation_id)
        if frames is None:
            raise ScopeViolation("observation was not produced by this adapter")
        return frames


def deadline_after(seconds: float, *, clock=time.monotonic) -> float:
    if seconds <= 0:
        raise ValueError("deadline interval must be positive")
    return clock() + seconds


def _candidate(
    frame: WindowFrame, region: VisualRegion, *, popup: bool = False, operation: str = "click"
) -> ObservedCandidate:
    rect = _checked_rect(region.rect, frame.width, frame.height)
    # The exact rect stays out of the id (see stage1_extractors._with_stable_ids); act() checks it.
    # Click ids predate fill and stay unsuffixed so retained picks and cached decisions keep matching.
    identity = f"{frame.hwnd}\0{frame.process_id}\0{region.id}" + ("" if operation == "click" else f"\0{operation}")
    scope = {"scope": "owned_popup", "scope_hwnd": frame.hwnd} if popup else {}
    return ObservedCandidate(
        id=hashlib.sha256(identity.encode()).hexdigest()[:16],
        operation=operation,
        label=region.label,
        subject=region.id,
        attributes={
            "rect": rect,
            # The full evidence string (word counts, pixel areas) only re-identifies; the provider
            # needs just which extractor backs the candidate and how sure it was.
            "source": region.evidence.split(":", 1)[0],
            "confidence": round(region.confidence, 2),
            # ADR-0038: the target is scrolled into view (and re-identified) before any input.
            **({"offscreen": "scrolled into view before input"} if region.evidence.endswith(":offscreen") else {}),
            **scope,
        },
        binding={"anchor_fingerprint": frame.anchor_fingerprint(rect), "evidence": region.evidence},
    )


def _fill_candidates(frame: WindowFrame, regions: Sequence[VisualRegion], *, popup: bool) -> list[ObservedCandidate]:
    """Per-region fills, except regions on one uniform panel, which become that panel's single fill (ADR-0029)."""

    pixels = np.frombuffer(frame.pixels, np.uint8).reshape(frame.height, frame.width, 4)
    eligible = [not region.evidence.startswith(EDGE_CONTOUR_SOURCE) for region in regions]
    blocks = panel_blocks(pixels, [region.rect for region in regions], eligible)
    absorbed = {index for block in blocks for index in block.members}
    captions = _field_captions(regions, absorbed)
    candidates = []
    for index, region in enumerate(regions):
        if index in absorbed or index in captions.values():
            continue
        candidate = _candidate(frame, region, popup=popup, operation="fill")
        if index in captions:
            candidate = _captioned(candidate, regions[captions[index]])
        candidates.append(candidate)
    scope = {"scope": "owned_popup", "scope_hwnd": frame.hwnd} if popup else {}
    for block in blocks:
        members = [regions[index] for index in block.members]
        rect = _checked_rect(block.rect, frame.width, frame.height)
        identity = f"{frame.hwnd}\0{frame.process_id}\0fill-panel\0" + "\0".join(sorted(m.id for m in members))
        candidates.append(
            ObservedCandidate(
                id=hashlib.sha256(identity.encode()).hexdigest()[:16],
                operation="fill",
                label=_short(" / ".join(m.label for m in members), FILL_PANEL_LABEL_CHARS),
                subject=f"panel:{members[0].id}",
                attributes={
                    "rect": rect,
                    "source": FILL_PANEL_SOURCE,
                    "confidence": round(min(m.confidence for m in members), 2),
                    **scope,
                },
                binding={
                    "anchor_fingerprint": frame.anchor_fingerprint(rect),
                    "evidence": f"{FILL_PANEL_SOURCE}:{rect}:{block.delivery_point}",
                    "delivery_point": block.delivery_point,
                    "members": tuple((m.id, m.label, m.rect, m.evidence) for m in members),
                },
            )
        )
    return candidates


def _is_fill_panel(candidate: ObservedCandidate) -> bool:
    return candidate.operation == "fill" and candidate.attributes.get("source") == FILL_PANEL_SOURCE


def _field_captions(regions: Sequence[VisualRegion], absorbed: set[int]) -> dict[int, int]:
    """Field index -> index of the "Name:"-style caption just left of it on the same row.

    Geometry only, not proof of an editable field (consult 20260925-1028 point 3): the pair must be
    one-to-one, and a caption inside another region may itself be a field's value.
    """

    def inside(inner: Rect, outer: Rect) -> bool:
        return (
            outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[0] + inner[2] <= outer[0] + outer[2] and inner[1] + inner[3] <= outer[1] + outer[3]
        )

    pairs: dict[int, list[int]] = {}
    for index, caption in enumerate(regions):
        text = caption.label.strip()
        if (
            index in absorbed
            or caption.evidence.startswith(EDGE_CONTOUR_SOURCE)
            or len(text) > FIELD_LABEL_MAX_CHARS
            or not text.endswith((":", "\uff1a"))
            or any(other is not caption and inside(caption.rect, other.rect) for other in regions)
        ):
            continue
        x, y, width, height = caption.rect
        right, middle = x + width, y + height / 2
        fields = [
            near
            for near, region in enumerate(regions)
            if near != index
            and near not in absorbed
            and 0 <= region.rect[0] - right <= FIELD_LABEL_MAX_GAP_LINES * height
            and region.rect[1] <= middle < region.rect[1] + region.rect[3]
            and y <= region.rect[1] + region.rect[3] / 2 < y + height
        ]
        if len(fields) == 1:
            pairs.setdefault(fields[0], []).append(index)
    field_indexes = set(pairs)
    return {
        field: owners[0]
        for field, owners in pairs.items()
        if len(owners) == 1 and owners[0] not in field_indexes
    }


def _captioned(candidate: ObservedCandidate, caption: VisualRegion) -> ObservedCandidate:
    """The field's own fill, labelled by its caption; the field's text goes out as its current value (observe)."""

    return ObservedCandidate(
        id=hashlib.sha256(f"{candidate.id}\0caption\0{caption.id}".encode()).hexdigest()[:16],
        operation=candidate.operation,
        label=caption.label.strip(),
        subject=candidate.subject,
        attributes={
            **candidate.attributes,
            "confidence": round(min(candidate.attributes["confidence"], caption.confidence), 2),
        },
        binding={
            **candidate.binding,
            "field_label": candidate.label,
            "caption": (caption.id, caption.label, caption.rect, caption.evidence),
        },
    )


def _fill_still_there(
    frame: WindowFrame, regions: Sequence[VisualRegion], wanted: ObservedCandidate, *, popup: bool
) -> bool:
    """Rebuilding the fills yields the same composite target: same box, members or caption, delivery point."""

    def key(candidate: ObservedCandidate):
        binding = {k: v for k, v in candidate.binding.items() if k != "anchor_fingerprint"}
        return candidate.id, candidate.attributes.get("rect"), binding

    return sum(key(candidate) == key(wanted) for candidate in _fill_candidates(frame, regions, popup=popup)) == 1


def _delivery_point(candidate: ObservedCandidate) -> tuple[int, int]:
    point = candidate.binding.get("delivery_point")
    return tuple(point) if point is not None else _center(_candidate_rect(candidate))  # type: ignore[return-value]


def _drag_start_candidate(frame: WindowFrame, region: VisualRegion, *, popup: bool) -> ObservedCandidate:
    candidate = _candidate(frame, region, popup=popup, operation="drag")
    return ObservedCandidate(
        candidate.id,
        candidate.operation,
        f"Choose drag start at '{region.label}'",
        candidate.subject,
        {**candidate.attributes, "drag_phase": "start"},
        {**candidate.binding, "frame_hwnd": frame.hwnd, "original_label": region.label},
    )


def _drag_end_candidate(
    start: ObservedCandidate,
    frame: WindowFrame,
    region: VisualRegion,
    *,
    popup: bool,
    base_observation_id: str,
    drop: bool = False,
) -> ObservedCandidate:
    end = _candidate(frame, region, popup=popup, operation="drag")
    start_hwnd = _scope_hwnd(start) or int(start.binding.get("frame_hwnd", frame.hwnd))
    end_hwnd = _scope_hwnd(end) or frame.hwnd
    identity = f"drag-end\0{start.id}\0{end.id}"
    source = str(end.attributes.get("source"))
    end_name = f"shape at {region.rect}" if source == EDGE_CONTOUR_SOURCE else f"'{region.label}'"
    return ObservedCandidate(
        hashlib.sha256(identity.encode()).hexdigest()[:16],
        "drag",
        f"Drag from '{start.binding.get('original_label', start.label)}' to {end_name}"
        + (" in the drop window" if drop else ""),
        end.subject,
        {
            **end.attributes,
            **({"scope": DROP_SCOPE} if drop else {}),
            "drag_phase": "end",
            "start_label": start.binding.get("original_label", start.label),
        },
        {
            **end.binding,
            "base_observation_id": base_observation_id,
            "start_hwnd": start_hwnd,
            "start_rect": start.attributes.get("rect"),
            "start_subject": start.subject,
            "start_label": start.binding.get("original_label", start.label),
            "start_evidence": start.binding.get("evidence"),
            "end_label": region.label,
        },
    )


def _valid_rect_value(value) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and value[2] > 0
        and value[3] > 0
    )


def _semantic_parts(frame: WindowFrame, regions: Sequence[VisualRegion], *, popup: bool) -> list[str]:
    prefix = f"{frame.hwnd}\0" if popup else ""
    return [f"{prefix}{region.id}\0{region.label}\0{region.rect}\0{region.evidence}" for region in regions]


def _uia_semantic_parts(frame: WindowFrame, regions: Sequence[VisualRegion], elements, *, popup: bool) -> list[str]:
    """The meaning a UIA candidate is judged by: as before ADR-0044, a UIA window's uncovered OCR is left out."""

    if elements:
        regions = [region for region in regions if _is_uia(region)]
    return _semantic_parts(frame, regions, popup=popup) + _value_parts(frame, elements or (), popup=popup)


def _digest(parts: Sequence[str]) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def _value_parts(frame: WindowFrame, elements: Sequence, *, popup: bool) -> list[str]:
    # E2E-03: a suggestion click filled the Email field; with the labels unchanged it read as no change and
    # the click was repeated three times.
    prefix = f"{frame.hwnd}\0" if popup else ""
    return [
        f"{prefix}value\0{_uia_region(element).id}\0{(element.value or '').replace(chr(0xFEFF), '').strip()}"
        for element in elements
        if _carries_value(element)
    ]


def _key_candidate(root: WindowFrame, key: str) -> ObservedCandidate:
    identity = f"{root.hwnd}\0{root.process_id}\0key\0{key}"
    return ObservedCandidate(
        id=hashlib.sha256(identity.encode()).hexdigest()[:16],
        operation="key",
        label=f"Press {key}",
        subject=f"key:{key}",
        attributes={"key": key, "source": "key"},
    )


def _scroll_candidates(
    frame: WindowFrame, regions: Sequence[VisualRegion], *, popup: bool
) -> list[ObservedCandidate]:
    candidates: list[ObservedCandidate] = []
    scope = {"scope": "owned_popup", "scope_hwnd": frame.hwnd} if popup else {}
    for stack in _row_stacks(frame, regions):
        rect = _bounding_rect([region.rect for region in stack])
        tops = sorted(region.rect[1] for region in stack)
        pitches = [lower - upper for upper, lower in zip(tops, tops[1:]) if lower > upper]
        pitch = sorted(pitches)[len(pitches) // 2] if pitches else max(region.rect[3] for region in stack)
        # About two thirds of the visible list per step keeps an overlap to read continuity; Windows
        # scrolls three lines per notch by default.
        notches = max(1, min(SCROLL_MAX_NOTCHES, round(rect[3] * 2 / 3 / pitch / 3)))
        first = _short(min(stack, key=lambda region: (region.rect[1], region.rect[0])).label)
        last = _short(max(stack, key=lambda region: (region.rect[1] + region.rect[3], -region.rect[0])).label)
        for direction in ("up", "down"):
            identity = f"{frame.hwnd}\0{frame.process_id}\0scroll\0{direction}\0{rect}"
            candidates.append(
                ObservedCandidate(
                    id=hashlib.sha256(identity.encode()).hexdigest()[:16],
                    operation="scroll",
                    label=f"Scroll {direction} the list from '{first}' to '{last}'",
                    subject=f"scroll:{direction}",
                    attributes={
                        "rect": rect, "direction": direction, "notches": notches, "source": "scroll", **scope,
                    },
                )
            )
    return candidates


def _row_stacks(frame: WindowFrame, regions: Sequence[VisualRegion]) -> list[list[VisualRegion]]:
    """Regions chained by overlapping x spans and small vertical gaps, at least SCROLL_MIN_ROWS long."""

    rows = [
        region
        for region in regions
        if not region.evidence.startswith(EDGE_CONTOUR_SOURCE)
        and region.rect[2] <= frame.width * SCROLL_MAX_WIDTH_RATIO
    ]
    parent = list(range(len(rows)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            (al, at, aw, ah), (bl, bt, bw, bh) = a.rect, b.rect
            if min(al + aw, bl + bw) <= max(al, bl):
                continue
            gap = max(bt - (at + ah), at - (bt + bh))
            if gap <= SCROLL_MAX_GAP_ROWS * max(ah, bh):
                parent[root(j)] = root(i)
    groups: dict[int, list[VisualRegion]] = {}
    for index, region in enumerate(rows):
        groups.setdefault(root(index), []).append(region)
    stacks = []
    for group in groups.values():
        # Words on one line share a row; the stack needs distinct lines to be a list.
        if len({region.rect[1] for region in group}) >= SCROLL_MIN_ROWS:
            stacks.append(sorted(group, key=lambda region: (region.rect[1], region.rect[0])))
    return sorted(stacks, key=lambda stack: (stack[0].rect[1], stack[0].rect[0]))


def _bounding_rect(rects: Sequence[Rect]) -> Rect:
    left = min(rect[0] for rect in rects)
    top = min(rect[1] for rect in rects)
    right = max(rect[0] + rect[2] for rect in rects)
    bottom = max(rect[1] + rect[3] for rect in rects)
    return left, top, right - left, bottom - top


def _contains(rect: Rect, point: tuple[int, int]) -> bool:
    left, top, width, height = rect
    x, y = point
    return left <= x < left + width and top <= y < top + height


def _short(label: str, limit: int = 24) -> str:
    return label if len(label) <= limit else label[: limit - 1] + "…"


def _uia_region(element) -> VisualRegion:
    # The id is what re-identification matches, so it is built from what a re-read reproduces.
    identity = hashlib.sha256(f"{element.role}\0{element.name}\0{element.rect}".encode()).hexdigest()[:16]
    offscreen = ":offscreen" if getattr(element, "offscreen", False) else ""
    label = element.name or f"({element.role})"
    return VisualRegion(f"uia-{identity}", label, element.rect, 1.0, f"uia:{element.role}{offscreen}")


def _is_uia(region: VisualRegion) -> bool:
    return region.evidence.startswith("uia:")


def _pointable(regions: Sequence[VisualRegion]) -> tuple[VisualRegion, ...]:
    return tuple(region for region in regions if not region.evidence.endswith(COVERED_SUFFIX))


def _ocr_only(regions: Sequence[VisualRegion]) -> tuple[VisualRegion, ...]:
    return tuple(region for region in regions if not _is_uia(region))


def _uia_evidence(candidate: ObservedCandidate) -> bool:
    return str(candidate.binding.get("evidence", "")).startswith("uia:")


def _uia_regions(elements: Sequence) -> tuple[VisualRegion, ...]:
    regions: dict[str, VisualRegion] = {}
    for element in elements:
        region = _uia_region(element)
        regions.setdefault(region.id, region)
    return tuple(regions.values())


def _uia_states(elements: Sequence) -> dict[str, tuple[str, bool]]:
    return {
        _uia_region(element).id: (element.checked, bool(getattr(element, "selects", False)))
        for element in elements
        if getattr(element, "checked", None) is not None
    }


def _with_state(candidate: ObservedCandidate, states: Mapping[str, tuple[str, bool]]) -> ObservedCandidate:
    if candidate.operation != "click" or candidate.subject not in states:
        return candidate
    checked, selects = states[candidate.subject]
    return replace(
        candidate,
        attributes={**candidate.attributes, "checked": checked},
        binding={**candidate.binding, "selects": selects},
    )


def _state_parts(frame: WindowFrame, states: Mapping[str, tuple[str, bool]], *, popup: bool) -> list[str]:
    # Toggling one control leaves every label as it was; without its state the screen reads as unchanged.
    prefix = f"{frame.hwnd}\0" if popup else ""
    return [f"{prefix}checked\0{region_id}\0{state[0]}" for region_id, state in sorted(states.items())]


def _uia_value_regions(elements: Sequence) -> tuple[VisualRegion, ...]:
    regions = []
    for element in elements:
        value = (element.value or "").replace("\ufeff", "").strip() if _carries_value(element) else ""
        if value and value != element.name and not getattr(element, "offscreen", False):
            identity = hashlib.sha256(f"value\0{element.role}\0{element.name}\0{element.rect}".encode()).hexdigest()[:16]
            regions.append(VisualRegion(f"uia-value-{identity}", value, element.rect, 1.0, "uia:value"))
    return tuple(regions)


def _uia_fill_candidates(frame: WindowFrame, elements: Sequence, *, popup: bool) -> list[ObservedCandidate]:
    candidates = []
    for element in elements:
        if not element.editable:
            continue
        candidate = _candidate(frame, _uia_region(element), popup=popup, operation="fill")
        value = (element.value or "").replace("\ufeff", "").strip()
        # What observe_window shows as the field's value; OCR cannot tell a placeholder from typed text (E2E-I39).
        candidate = replace(candidate, binding={**candidate.binding, "field_value": value})
        if value and value != element.name:
            # The field's current text, apart from its name (BUG-0034); untrusted like any screen text.
            candidate = replace(candidate, binding={**candidate.binding, "field_label": value})
        if getattr(element, "invalid", False):
            candidate = replace(candidate, attributes={**candidate.attributes, "invalid": "value rejected by the form"})
        candidates.append(candidate)
    return candidates


def _carries_value(element) -> bool:
    return element.editable or getattr(element, "range", None) is not None


def _uia_range_candidates(frame: WindowFrame, elements: Sequence, *, popup: bool) -> list[ObservedCandidate]:
    candidates = []
    for element in elements:
        if getattr(element, "range", None) is None or getattr(element, "offscreen", False):
            continue
        candidate = _candidate(frame, _uia_region(element), popup=popup, operation="set_range")
        low, high = element.range
        candidate = replace(
            candidate,
            binding={
                **candidate.binding,
                "field_value": element.value or "",
                "field_label": element.value or "",
                "range": (low, high),
            },
            attributes={**candidate.attributes, "range": f"{low:g}..{high:g}"},
        )
        candidates.append(candidate)
    return candidates


def _clipped(rect: Rect, frame: WindowFrame) -> Rect | None:
    left, top = max(0, rect[0]), max(0, rect[1])
    right, bottom = min(frame.width, rect[0] + rect[2]), min(frame.height, rect[1] + rect[3])
    return (left, top, right - left, bottom - top) if right > left and bottom > top else None


def _reticle_action(operation: str) -> str:
    return "click" if operation in POINTER_OPERATIONS else "type"


def _equivalent_frames(before: Sequence[WindowFrame], after: Sequence[WindowFrame]) -> bool:
    """Same windows whose pixels are identical or differ only by one text caret.

    A caret blinking in the phase opposite to the observation flips OCR of its whole line (VSCode read
    OLDVALUE as DLDVALUE with a 2x19px caret before it, plan screen-input-ops), so no region-level
    comparison survives it. Judging the diff's shape instead of waiting for a phase works for any
    blink period; two carets or a moved caret span a wider box and stay a change."""

    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        if old.hwnd != new.hwnd or (old.width, old.height) != (new.width, new.height):
            return False
        if old.pixels == new.pixels:
            continue
        changed = np.any(
            np.frombuffer(old.pixels, np.uint8).reshape(old.height, old.width, 4)[:, :, :3]
            != np.frombuffer(new.pixels, np.uint8).reshape(new.height, new.width, 4)[:, :, :3],
            axis=2,
        )
        rows = np.flatnonzero(changed.any(axis=1))
        columns = np.flatnonzero(changed.any(axis=0))
        if rows.size == 0:
            continue
        width = int(columns[-1] - columns[0] + 1)
        height = int(rows[-1] - rows[0] + 1)
        if width > CARET_MAX_WIDTH or height > CARET_MAX_HEIGHT or height < 2 * width:
            return False
    return True


def _observation_id(frames: Sequence[WindowFrame]) -> str:
    if len(frames) == 1:
        return frames[0].fingerprint
    body = "\0".join(f"{frame.hwnd}:{frame.fingerprint}" for frame in frames)
    return hashlib.sha256(body.encode()).hexdigest()


def _scope_hwnd(candidate: ObservedCandidate) -> int | None:
    value = candidate.attributes.get("scope_hwnd")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _frame_by_hwnd(frames: Sequence[WindowFrame], hwnd: int) -> WindowFrame | None:
    return next((frame for frame in frames if frame.hwnd == hwnd), None)


def _checked_rect(rect: Rect, frame_width: int, frame_height: int) -> Rect:
    if len(rect) != 4 or any(isinstance(value, bool) or not isinstance(value, int) for value in rect):
        raise ValueError("visual region rect must contain four integers")
    left, top, width, height = rect
    if left < 0 or top < 0 or width <= 0 or height <= 0:
        raise ValueError("visual region rect must be positive and inside the frame")
    if left + width > frame_width or top + height > frame_height:
        raise ValueError("visual region rect must be inside the frame")
    return rect


def _candidate_rect(candidate: ObservedCandidate) -> Rect:
    raw = candidate.attributes.get("rect")
    if not isinstance(raw, (tuple, list)) or len(raw) != 4:
        raise ValueError("visual candidate has no valid rect")
    return tuple(raw)  # type: ignore[return-value]


def _center(rect: Rect) -> tuple[int, int]:
    left, top, width, height = rect
    return left + width // 2, top + height // 2


def _same_scope(original: WindowFrame, current: WindowFrame) -> bool:
    return (original.hwnd, original.process_id, original.width, original.height) == (
        current.hwnd,
        current.process_id,
        current.width,
        current.height,
    )
