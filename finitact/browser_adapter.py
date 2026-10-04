"""The browser path as an ``ActionAdapter`` for the shared loop (ADR-0037 step 1).

A thin wrapper: DOM reading, unique rebinding, delivery and post-input settling stay in ``Browser``. What the
shared loop cannot know generically is exposed here: the browser's own decision request and text context, the
allowed-origin gate, and ``wait`` as a non-mutating choice.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from .action_adapter import (
    Freshness, MutationResult, MutationUncertain, Observation, ScopeViolation, checked_effect, postcondition,
)
from .browser import MutationUncertain as BrowserMutationUncertain
from .browser import StalePage
from .contracts import ObservedCandidate, decision_request
from .model import field_context

# Chosen like an action but never an input; it is not a mutation and not a no-progress repeat (agent.py).
NON_MUTATING = frozenset({"wait"})


def origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


class BrowserAdapter:
    ownership = "owned"
    non_mutating = NON_MUTATING
    # History and provider attempts span the run's goals, as Agent.continue_with kept them.
    budget_scope = "run"

    def __init__(
        self, browser, *, allowed_origins: Sequence[str], screenshots: bool = False, drag: bool = False
    ) -> None:
        self.browser = browser
        # ADR-0046: drag is opt-in; without it no drag candidate exists, so ordinary runs offer what they always did.
        self.drag = drag
        self.allowed = {origin(item) for item in allowed_origins}
        self.screenshots = screenshots
        self._pages: dict[str, Mapping[str, Any]] = {}
        self.page: Mapping[str, Any] | None = None

    def observe(self) -> Observation:
        page = self.browser.observe(screenshot=self.screenshots)
        if self.drag:
            page = {**page, "actions": [*page["actions"], *_drag_starts(page)]}
        self._remember(page)
        return self._observation(page)

    def fresh(self, observation: Observation, candidate: ObservedCandidate | None = None) -> Freshness:
        page = self._page_for(observation)
        action = self._action(page, candidate) if candidate is not None else None
        # Browser.fresh rebinds a regenerated but unique target in place; that is FRESH, not a new choice.
        return Freshness.FRESH if self.browser.fresh(page, action) else Freshness.STALE

    def act(self, candidate: ObservedCandidate, observation: Observation, text: str | None = None) -> MutationResult:
        page = self._page_for(observation)
        action = self._action(page, candidate)
        if action is None:
            return MutationResult(candidate.id, "pattern", "not_attempted", "candidate is not in the observation")
        try:
            result = self.browser.act(action, page, text=text)
        except StalePage as exc:
            # Checked before any input (Browser.act re-checks freshness first): safe to choose again.
            return MutationResult(candidate.id, "pattern", "not_attempted", f"stale_before_input: {exc}")
        except BrowserMutationUncertain as exc:
            raise MutationUncertain(str(exc)) from exc
        if isinstance(result, Mapping) and result.get("pending"):
            # ADR-0047: a dialog opened while the input waited; it is delivered only after the dialog is answered.
            return MutationResult(candidate.id, "pattern", "pending", "dialog opened before delivery was confirmed")
        return MutationResult(candidate.id, "pattern", "confirmed")

    def effect(self, candidate: ObservedCandidate, before: Observation, after: Observation, text: str | None):
        """ADR-0039: whether the control reached the state this operation means; None when it means no state."""

        action = self._action(self._page_for(before), candidate)
        if action is None or action.get("node") is None:
            return None
        node, kind = action["node"], action["kind"]
        later = [item for item in self._page_for(after)["actions"] if item.get("node") == node]
        if kind == "click" and action.get("checked") in ("true", "false"):
            observed = next((item["checked"] for item in later if item.get("checked") is not None), None)
            return checked_effect(action["label"], action.get("role") == "radio", action["checked"], observed)
        if kind == "click" and action.get("role") == "option":
            # BUG-0023: a custom listbox/combobox option (not a native <select>) is usually removed from the
            # DOM when the list closes, so ``later`` (matched by this node) is empty; the effect instead shows
            # on the trigger that had the list open.
            return _option_effect(action, self._page_for(before)["actions"], self._page_for(after)["actions"])
        if kind == "select":
            observed = next((item["current_value"] for item in later if item.get("kind") == "select"), None)
            return postcondition(action["label"], "value", action.get("option"), action.get("current_value"), observed)
        if kind == "fill" and text is not None:
            observed = next((item.get("value") for item in later if item.get("kind") == "fill"), None)
            result = postcondition(action["label"], "value", text, action.get("value"), observed)
            # A page may reformat what was typed (42500 -> 42,500): only an empty field is a clear miss.
            if result["effect"] == "not_met" and observed:
                result["effect"] = "unknown"
            return result
        return None

    def fit_view(self, observation: Observation) -> Mapping[str, Any]:
        from .browser_inventory import final_state

        return final_state(self._page_for(observation))

    def until_baseline(self) -> frozenset:
        return frozenset(getattr(self.browser, "download_state", None) or ())

    def until_unmet(self, until, observation: Observation, baseline: frozenset, settle_s: float = 0.0) -> list[str]:
        from .browser_inventory import until_unmet

        downloads = None
        if until.download:
            self.browser.downloads(settle_s)
            state = getattr(self.browser, "download_state", None)
            if state is not None:
                downloads = [item for guid, item in state.items() if guid not in baseline]
        return until_unmet(until, self._page_for(observation), downloads)

    def check_scope(self, observation: Observation) -> None:
        if origin(self._page_for(observation)["url"]) not in self.allowed:
            raise ScopeViolation("page origin is outside allowed_origins")

    def decision_request(self, observation: Observation, goal: str, history, remaining_attempts: int, _inputs=None):
        # The browser keeps its typed operation/target question; switching providers' mode is a separate change.
        # Candidates are the loop's allowed ones, so a denied kind is no longer offered only to be refused.
        request = decision_request(self._page_for(observation), goal, history, remaining_attempts)
        return replace(request, candidates=tuple(observation.candidates))

    def text_context(self, observation: Observation, candidate: ObservedCandidate, goal: str, history) -> Mapping:
        page = self._page_for(observation)
        return field_context(goal, self._action(page, candidate), page, history)

    def close(self) -> None:
        self.browser.close()

    def drag_end_observation(self, start: ObservedCandidate, observation: Observation) -> Observation:
        """Turn a chosen drag start into one candidate per observed end (the loop's two-step drag, ADR-0045)."""

        page = self._page_for(observation)
        action = self._action(page, start)
        if (not self.drag or action is None or action["kind"] != "drag"
                or action.get("drag_phase") != "start"):
            raise ScopeViolation("candidate is not an offered drag start")
        ends = _drag_ends(page, action)
        identity = f"{observation.observation_id}\0drag-end\0{start.id}"
        semantic = f"{observation.semantic_id}\0drag-end\0{start.id}"
        end_page = {
            **page,
            "fingerprint": hashlib.sha256(identity.encode()).hexdigest(),
            "semantic_fingerprint": hashlib.sha256(semantic.encode()).hexdigest(),
            "actions": ends,
        }
        self._remember(end_page)
        self.page = page  # the run's final state is the page, not the list of drag ends
        return self._observation(end_page)

    def _remember(self, page: Mapping[str, Any]) -> None:
        self.page = page
        self._pages[page["fingerprint"]] = page
        while len(self._pages) > 16:
            self._pages.pop(next(iter(self._pages)))

    def _observation(self, page: Mapping[str, Any]) -> Observation:
        return Observation(
            page["fingerprint"],
            page["semantic_fingerprint"],
            tuple(ObservedCandidate.from_action(action) for action in page["actions"]),
            True,
        )

    def _page_for(self, observation: Observation) -> Mapping[str, Any]:
        page = self._pages.get(observation.observation_id)
        if page is None:
            raise ScopeViolation("observation was not produced by this adapter")
        return page

    @staticmethod
    def _action(page: Mapping[str, Any], candidate: ObservedCandidate | None):
        if candidate is None:
            return None
        return next((action for action in page["actions"] if action["id"] == candidate.id), None)


BrowserFactory = Callable[[str], Any]

MAX_DRAG_ENDS = 60
# A drop can land on a control as well as on a marked area (a trash button, another list row).
DROP_ACTION_KINDS = frozenset({"click"})


def _drag_starts(page: Mapping[str, Any]) -> list[dict]:
    return [
        {"id": f"d{index}", "kind": "drag", "label": f"Choose drag start at '{item['label']}'",
         "node": item["node"], "rect": item["rect"], "drag_phase": "start", "start_label": item["label"]}
        for index, item in enumerate(page.get("drag_sources") or [], 1)
    ]


def _drag_ends(page: Mapping[str, Any], start: Mapping[str, Any]) -> list[dict]:
    """Where the chosen source can be released: drop areas, other sources, then plain controls, each once."""

    seen = {start["node"]}
    ends: list[dict] = []

    def add(node, label, rect, role):
        if node in seen or len(ends) >= MAX_DRAG_ENDS:
            return
        seen.add(node)
        ends.append(
            {"id": f"{start['id']}>{len(ends) + 1}", "kind": "drag", "node": node, "rect": rect,
             "label": f"Drag from '{start['start_label']}' to '{label}'", "drag_phase": "end",
             "start_label": start["start_label"], "from_node": start["node"], "role": role}
        )

    for item in page.get("drop_targets") or []:
        add(item["node"], item["label"], item["rect"], "drop area")
    for item in page.get("drag_sources") or []:
        add(item["node"], item["label"], item["rect"], "draggable")
    for action in page["actions"]:
        if action["kind"] in DROP_ACTION_KINDS and not action.get("offscreen") and action.get("node") is not None:
            add(action["node"], action["label"], action["rect"], action.get("role") or "control")
    return ends


def _option_effect(action: Mapping[str, Any], before_actions, after_actions) -> dict | None:
    """The trigger that had this option's list open, now collapsed and showing the option (BUG-0023).

    Exactly one currently-expanded control identifies the list this option belongs to; more or fewer than
    one is ambiguous and gets no verdict, same as a plain click with no post-condition.
    """

    triggers = [item for item in before_actions
                if item.get("expanded") == "true" and item.get("node") != action["node"]]
    if len(triggers) != 1:
        return None
    before_trigger = triggers[0]
    after_trigger = next((item for item in after_actions if item.get("node") == before_trigger["node"]), None)
    if after_trigger is None:
        return None
    if after_trigger.get("expanded") == "true":
        effect = "not_met"
    else:
        option_text = "".join(str(action["label"]).split()).casefold()
        after_text = "".join(str(after_trigger.get("label", "")).split()).casefold()
        before_text = "".join(str(before_trigger.get("label", "")).split()).casefold()
        effect = "met" if option_text and option_text in after_text and after_text != before_text else "unknown"
    return {"target": action["label"], "expected": {"value": action["label"]},
            "before": before_trigger.get("label"), "after": after_trigger.get("label"), "effect": effect}
