"""Provisional, adapter-agnostic contract for observe/candidate/act GUI adapters.

Windows UI Automation is the first non-browser action adapter (ADR-0004, Phase 4). ADR-0004
deliberately defers unifying this with Browser's own observe/fresh/act (finitact/browser.py)
until a live UIA prototype proves what generalizes: this module is the Windows-side contract
only, not a shared base class.

The shape and every field here responds to a concrete gap found either in a claude opus
design review or in a live windows-mcp check against this machine's Notepad
(docs/consult/20260921-1819-20260921-windows-adapter.md):

- `Observation.complete` / `read_errors`: a UIA tree walk is not one atomic call the way
  Browser's single `Runtime.evaluate` read is (browser.py:61-104). A partial walk must not be
  handed to a decision provider as if it were a full candidate set.
- `Observation.observation_id` / `semantic_id`: kept as two values, matching Browser's
  `fingerprint`/`semantic_fingerprint` split (browser.py:159-177), because agent.py's
  same-action-three-times / semantic-unchanged detection depends on both.
- `Freshness` has three states, not two: UIA elements are re-created far more often than DOM
  nodes are, and Browser's own `fresh() -> bool` already throws away the fact that it just
  rebound an action to a different node (browser.py:105-142) — that silent rebind should not
  be repeated here.
- `MutationResult.input_method`: UIA's `ValuePattern.SetValue` is atomic and focus-independent;
  synthetic key/pointer input is focus-dependent and can leak into another window if focus was
  lost. These are different risk classes and the caller must be able to tell them apart.
  `synthetic_pointer` is split out from `synthetic_key` because opening a context menu (Phase A,
  docs/plans/finitact-desktop-generalization.md) has no pattern equivalent and must go through
  synthetic right-click or Shift+F10/VK_APPS — same focus-dependent risk class as `synthetic_key`,
  named separately so a caller auditing MutationResult history can tell which raw delivery
  mechanism actually fired (relevant given the real windows-mcp mis-delivery incident this ADR's
  현황 log records).
- Context menus (Phase A) need no new adapter method: `act()` on an `OPEN_CONTEXT_MENU` candidate
  (finitact/operation_vocabulary.py) is an ordinary mutation, and the menu's items are ordinary
  InvokePattern candidates the next `observe()` picks up — the existing observe/act/observe cycle
  already is the "two-stage observation" the plan asked for. The one real contract gap: a context
  menu is a transient *popup window*, not a descendant node of the element that spawned it, so
  `ScopeViolation` must be judged against the adapter's owning-window tree (the configured
  top-level window plus windows it owns, e.g. `GetWindow(GW_OWNER)`), not literal UIA-tree
  descendance of one HWND — otherwise every context-menu candidate either goes missing from
  `observe()` or wrongly raises `ScopeViolation`. A window with no ownership link to the
  configured target is still out of scope and must still raise.
- A concrete adapter may classify an HWND-addressed message as `window_message` when it avoids
  the shared OS input queue and confirms a target-scoped postcondition before returning. This is
  not interchangeable with `pattern`, and message acceptance alone is never confirmation.
- `ownership`: an adapter attached to a pre-existing process must never have `close()` mean
  "terminate it" the way Browser's `close()` means "close the target I created"
  (browser.py:153-157).
- Untrusted values never enter `ObservedCandidate.attributes` (that would regress the
  `untrusted_context` separation Phase 2 already established, contracts.py:46,89-93): a control's
  current text/value is untrusted page content and belongs in `Observation.untrusted_values`,
  keyed by candidate id, so a request builder can route it the same way
  `decision_request()` already routes browser page text.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping, Protocol, Sequence

from .contracts import ObservedCandidate


class Freshness(Enum):
    FRESH = "fresh"
    REBOUND = "rebound"
    STALE = "stale"


class ScopeViolation(ValueError):
    """The adapter's configured window/process identity no longer matches what it observes.

    Distinct from Freshness.STALE: a stale candidate means "observe again"; a scope
    violation means "stop", because re-observing could otherwise act on a different window.

    "Configured identity" is the target's owning-window tree (the top-level window plus any
    windows it owns, e.g. a context menu popup opened from it via OPEN_CONTEXT_MENU), not a
    single literal HWND: a legitimately-spawned popup is in scope, an unrelated window is not.
    See action_adapter.py's module docstring for why context menus need this distinction.
    """


class MutationUncertain(RuntimeError):
    """Input delivery outcome is unknown; the caller must not retry automatically."""


@dataclass(frozen=True)
class Observation:
    observation_id: str
    semantic_id: str
    candidates: Sequence[ObservedCandidate]
    complete: bool
    untrusted_values: Mapping[str, str] = field(default_factory=dict)
    read_errors: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self):
        if not self.complete and self.candidates:
            raise ValueError("An incomplete observation must not carry candidates")


@dataclass(frozen=True)
class MutationResult:
    candidate_id: str
    input_method: Literal["pattern", "window_message", "synthetic_key", "synthetic_pointer"]
    status: Literal["confirmed", "pending", "uncertain", "not_attempted"]
    detail: str | None = None
    # A fill's click put the text caret in an input, so the keys went to one (not a guess from OCR).
    input_focus: bool = False


@dataclass(frozen=True)
class ActionOutcome:
    """One confirmed action and what was observed around it, for judging whether it achieved the goal."""

    goal: str
    candidate: ObservedCandidate
    text: str | None
    before: Observation
    after: Observation
    # The adapter's own evidence of the acted-on scope (screen_grounded_adapter.ScreenEvidence), or None.
    before_screen: Any
    after_screen: Any
    attempts: list
    attempt_limit: int | None
    call_id: str
    # Visible top-level windows around the action (windows_inventory.list_windows), or None when not probed.
    windows_before: tuple | None = None
    windows_after: tuple | None = None
    # The goal's caller-given fill text, which a send-like action should move out of its field.
    goal_text: str | None = None
    # Earlier confirmed actions for the same goal ({"operation", "target_label"}), oldest first.
    prior_actions: tuple = ()
    # ADR-0039's post-condition of this action ({"expected", "before", "after", "effect"}), when the adapter has one.
    effect: Mapping | None = None
    # The acted-on state as the adapter shows it to a fit question (the browser's page summary), or None.
    before_view: Mapping | None = None
    # MutationResult.input_focus of this action.
    input_focus: bool = False
    # The root window's own evidence before the action, for a popup-scoped candidate only (BUG-0023); else None.
    before_anchor_screen: Any = None


class ActionAdapter(Protocol):
    """One configured, scope-bound target. Not thread-safe; one run owns one instance.

    Implementations must route every synthetic input path through the Phase B interaction
    lease guard.  Pattern failure must never silently fall back to synthetic input.
    """

    ownership: Literal["owned", "attached"]

    def observe(self) -> Observation: ...

    def fresh(self, observation: Observation, candidate: ObservedCandidate | None = None) -> Freshness: ...

    def act(
        self, candidate: ObservedCandidate, observation: Observation, text: str | None = None
    ) -> MutationResult: ...

    def close(self) -> None:
        """`owned`: end the target process. `attached`: release the connection only."""


def postcondition(target: str, field: str, expected, before, observed) -> dict:
    """ADR-0039's result shape, shared by every adapter that can read the control's state back."""

    if expected is None or observed is None:
        effect = "unknown"
    else:
        effect = "met" if _same(observed, expected) else "not_met"
    return {"target": target, "expected": {field: expected}, "before": before, "after": observed, "effect": effect}


def checked_effect(target: str, selects: bool, before: str | None, observed: str | None) -> dict:
    """A click selects a radio-like item and flips a checkbox or switch ("true"/"false"/"mixed")."""

    expected = "true" if selects else {"true": "false", "false": "true"}.get(before or "")
    return postcondition(target, "checked", expected, before, observed)


def _same(observed, expected) -> bool:
    return "".join(str(observed).split()).casefold() == "".join(str(expected).split()).casefold()
