"""UIA candidate policy: which patterns are actionable, and which visibility state is candidate-worthy.

Mirrors the role-to-operation mapping `snapshot.js` performs for the browser DOM
(finitact/snapshot.js:71-99, 118-133): there, an element's ARIA role plus its editable-ness
decides whether it becomes a `fill` candidate, a `click` candidate, or both (an editable combobox
yields a `fill` action and a separate `click` "Open X" action from the same element, rather than
collapsing to one). This module makes the same choice for UI Automation controls, using the
patterns `AutomationElement.GetSupportedPatterns()` actually reports instead of an ARIA role,
because UIA has no role attribute of its own that predicts operability the way ARIA does.

Deliberately platform-inert: plain strings in, plain strings out. UIA itself only exists on
Windows (ADR-0006), so this module is testable from this Linux repo without a live UIA
implementation. A concrete Windows adapter passes the exact `GetSupportedPatterns()` programmatic
names it reads at runtime straight into `operations_for`.

A candidate whose supported patterns match none of these yields no operation — the caller must
treat it as `unsupported_control` (ADR-0006), not silently drop it or guess an action for it.

Reference: docs/plans/finitact-desktop-generalization.md Phase A.
"""

from typing import AbstractSet

# UIA pattern programmatic names, exactly as AutomationElement.GetSupportedPatterns() reports
# them (e.g. "InvokePatternIdentifiers.Pattern") — not abbreviations, so a caller can pass that
# call's output straight through without translation.
INVOKE_PATTERN = "InvokePatternIdentifiers.Pattern"
VALUE_PATTERN = "ValuePatternIdentifiers.Pattern"
TOGGLE_PATTERN = "TogglePatternIdentifiers.Pattern"
SELECTION_ITEM_PATTERN = "SelectionItemPatternIdentifiers.Pattern"
EXPAND_COLLAPSE_PATTERN = "ExpandCollapsePatternIdentifiers.Pattern"
RANGE_VALUE_PATTERN = "RangeValuePatternIdentifiers.Pattern"

# Not a pattern-derived operation: UIA has no SupportedPatterns entry for "show context menu"
# the way it does for click/fill/toggle/etc. Right-click is a raw synthetic pointer gesture
# (or Shift+F10/VK_APPS, a raw synthetic key), not a pattern invoke, so `operations_for` never
# returns it. A concrete adapter attaches OPEN_CONTEXT_MENU to a candidate as its own policy
# decision (e.g. by control type) and must execute it with `input_method="synthetic_pointer"`
# or `"synthetic_key"` (action_adapter.py), not `"pattern"`. Once the menu is open, its items are
# ordinary InvokePattern elements picked up by the normal candidate enumeration on the next
# `observe()` — see action_adapter.py's module docstring for the two-stage observe/act/observe
# contract this implies. Reference: docs/plans/finitact-desktop-generalization.md Phase A.
OPEN_CONTEXT_MENU = "open_context_menu"

# Also not pattern-derived: UIA SetFocus works on any keyboard-focusable element. An adapter offers it for
# text inputs, whose only pattern operation is fill; without it "click the field" had no candidate and the
# provider chose fill with nothing to type (E2E-I36).
FOCUS = "focus"

# Every pattern this module knows how to turn into an operation, in a fixed emission order so
# `operations_for` output is deterministic when a control supports several at once.
_PATTERN_TO_OPERATION = (
    (VALUE_PATTERN, "fill"),
    (TOGGLE_PATTERN, "toggle"),
    (SELECTION_ITEM_PATTERN, "select"),
    (RANGE_VALUE_PATTERN, "set_range"),
    (EXPAND_COLLAPSE_PATTERN, "expand_collapse"),
    (INVOKE_PATTERN, "click"),
)


def operations_for(supported_patterns: AbstractSet[str]) -> tuple[str, ...]:
    """Every canonical operation available on a control that supports `supported_patterns`.

    Returns one operation per matching pattern, not a single collapsed guess: a control that
    supports both ValuePattern and ExpandCollapsePattern (an editable combobox) yields both
    `fill` and `expand_collapse`, so a decision provider picks whichever the goal needs instead
    of the adapter deciding for it. Empty result means `unsupported_control`.

    Never returns `OPEN_CONTEXT_MENU`: that operation has no supporting pattern to key off of
    and is the adapter's own policy decision, not a mapping this function can make.
    """
    return tuple(operation for pattern, operation in _PATTERN_TO_OPERATION if pattern in supported_patterns)


def is_candidate_visible(*, is_enabled: bool, is_offscreen: bool) -> bool:
    """Whether a UIA element in this state should ever become a candidate.

    Mirrors `visible()`/`targetable()` in finitact/snapshot.js:10-24, which browser candidates
    must pass before a role is even considered. `IsEnabled` alone (what the Notepad prototype
    checked, docs/evaluations/windows-adapter-prototype/prototype-lib.ps1) is not enough: an
    element can be enabled but scrolled out of view or on a hidden tab, which UIA reports via
    `IsOffscreen`. Handing an offscreen-but-enabled element to a decision provider as if it were
    actionable is the UIA equivalent of browser's now-excluded `aria-hidden`/`inert` elements.
    """
    return is_enabled and not is_offscreen
