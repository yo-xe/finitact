"""Windows OutcomeVerifier (runs.py's protocol) built on owned-window enumeration.

Target-window re-observation only sees the acted-on HWND. A state change that renders as its own
top-level window -- a popup owned by the target process, not a child of it -- stays invisible to
that re-observation (Unity's EditorGUI dropdowns, Win32 common dialogs). This was a live false
negative once: a SendInput click's target-window re-capture looked unchanged while an owned popup
had, in fact, opened (phase-e-screen-grounded-seams.md, 2026-09-22).

``WindowsOwnedWindowVerifier`` closes that blind spot generically: it snapshots the process's
owned windows at the start of each goal and diffs each call's live snapshot against that baseline. It never infers
success from window titles or classes on its own -- what a given diff *means* is app-specific,
and a verifier meant to work across apps cannot honestly guess that meaning. The diff is handed to
a caller-supplied ``judge`` that makes the actual call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .action_adapter import Observation
from .runs import Goal
from .windows_screen_grounded import OwnedWindow, WindowsOwnedWindowEnumerator


@dataclass(frozen=True)
class OwnedWindowDiff:
    appeared: tuple[OwnedWindow, ...]
    disappeared: tuple[OwnedWindow, ...]

    @property
    def changed(self) -> bool:
        return bool(self.appeared or self.disappeared)


Judge = Callable[[Goal, Observation, OwnedWindowDiff], "bool | None"]


class WindowsOwnedWindowVerifier:
    """Adapts an owned-window diff into runs.py's ``OutcomeVerifier`` protocol.

    ``WindowsRunCoordinator`` calls ``begin_goal`` before a goal's first verification, so repeated
    calls across that goal's action loop compare against the state before the goal started rather
    than against the previous call. A goal id alone cannot mark that boundary: separate runs in one
    server reuse ids like ``g1``. Verifying without a baseline raises, which the coordinator records
    as an error instead of judging against a stale snapshot.
    """

    def __init__(self, enumerator: WindowsOwnedWindowEnumerator, process_id: int, judge: Judge) -> None:
        self.enumerator = enumerator
        self.process_id = process_id
        self.judge = judge
        self._baseline: Sequence[OwnedWindow] | None = None

    def begin_goal(self, _goal: Goal) -> None:
        self._baseline = self.enumerator.list_visible(self.process_id)

    def __call__(self, goal: Goal, observation: Observation) -> bool | None:
        return self.judge(goal, observation, self._diff())

    def _diff(self) -> OwnedWindowDiff:
        if self._baseline is None:
            raise RuntimeError("begin_goal was not called before verification")
        current = self.enumerator.list_visible(self.process_id)
        before = {window.hwnd: window for window in self._baseline}
        after = {window.hwnd: window for window in current}
        appeared = tuple(window for hwnd, window in after.items() if hwnd not in before)
        disappeared = tuple(window for hwnd, window in before.items() if hwnd not in after)
        return OwnedWindowDiff(appeared, disappeared)


WindowMatch = Callable[[OwnedWindow], bool]


def class_is(class_name: str) -> WindowMatch:
    return lambda window: window.class_name == class_name


def title_is(title: str) -> WindowMatch:
    return lambda window: window.title == title


def window_appeared(match: WindowMatch, *, verdict: bool = True) -> Judge:
    """``verdict`` when a matching window appeared; otherwise no verdict.

    Absence never becomes ``False``: the window may simply not have rendered yet, and turning that
    into a verified failure would stop a goal that is still in progress. Pass ``verdict=False`` only
    for a window whose appearance itself proves failure (an app's error dialog).
    """

    def judge(_goal: Goal, _observation: Observation, diff: OwnedWindowDiff) -> bool | None:
        return verdict if any(match(window) for window in diff.appeared) else None

    return judge


def window_disappeared(match: WindowMatch, *, verdict: bool = True) -> Judge:
    """``verdict`` when a matching baseline window is gone; otherwise no verdict."""

    def judge(_goal: Goal, _observation: Observation, diff: OwnedWindowDiff) -> bool | None:
        return verdict if any(match(window) for window in diff.disappeared) else None

    return judge


def by_goal_id(judges: Mapping[str, Judge]) -> Judge:
    """Dispatch on ``goal.id``; a goal without a registered judge gets no verdict."""

    def judge(goal: Goal, observation: Observation, diff: OwnedWindowDiff) -> bool | None:
        selected = judges.get(goal.id)
        return None if selected is None else selected(goal, observation, diff)

    return judge


def first_verdict(*judges: Judge) -> Judge:
    """The first non-``None`` verdict, so a failure oracle listed first outranks a success one."""

    def judge(goal: Goal, observation: Observation, diff: OwnedWindowDiff) -> bool | None:
        for candidate in judges:
            verdict = candidate(goal, observation, diff)
            if verdict is not None:
                return verdict
        return None

    return judge
