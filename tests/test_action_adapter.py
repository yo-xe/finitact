"""Characterization tests for the provisional ActionAdapter contract (ADR-0006).

A fake stands in for a real UIA adapter (Windows-only, not testable from this Linux repo).
These tests pin the contract shape itself: what Phase 4's live prototype must satisfy.
"""

import pytest

from finitact.action_adapter import (
    ActionAdapter,
    Freshness,
    MutationResult,
    Observation,
    ScopeViolation,
)
from finitact.contracts import ObservedCandidate
from finitact.operation_vocabulary import OPEN_CONTEXT_MENU


def candidate(id="c1", operation="click", label="Save"):
    return ObservedCandidate(id=id, operation=operation, label=label, subject="42")


class FakeAdapter:
    """A minimal in-memory ActionAdapter: one attached window, one candidate."""

    def __init__(self, *, ownership="owned"):
        self.ownership = ownership
        self.closed_as = None
        self._candidate = candidate()
        self._value = ""

    def observe(self) -> Observation:
        return Observation(
            observation_id="obs-1",
            semantic_id="sem-1",
            candidates=(self._candidate,),
            complete=True,
            untrusted_values={self._candidate.id: self._value},
        )

    def fresh(self, observation, candidate=None):
        if candidate is None:
            return Freshness.FRESH
        return Freshness.FRESH if candidate.id == self._candidate.id else Freshness.STALE

    def act(self, candidate, observation, text=None):
        if self.fresh(observation, candidate) is not Freshness.FRESH:
            raise ScopeViolation("candidate is not in the configured scope")
        if text is not None:
            self._value = text
            return MutationResult(candidate.id, "pattern", "confirmed")
        return MutationResult(candidate.id, "pattern", "confirmed")

    def close(self):
        self.closed_as = self.ownership


def test_fake_adapter_satisfies_the_protocol_shape():
    adapter: ActionAdapter = FakeAdapter()
    observation = adapter.observe()
    assert observation.complete
    assert observation.candidates[0].id == "c1"


def test_incomplete_observation_must_not_carry_candidates():
    with pytest.raises(ValueError, match="incomplete"):
        Observation(observation_id="o", semantic_id="s", candidates=(candidate(),), complete=False)


def test_untrusted_values_are_keyed_separately_from_candidate_attributes():
    adapter = FakeAdapter()
    adapter.act(candidate(), adapter.observe(), text="draft body")
    observation = adapter.observe()
    assert observation.untrusted_values["c1"] == "draft body"
    assert "value" not in observation.candidates[0].attributes


def test_fresh_reports_three_states_not_a_bare_bool():
    adapter = FakeAdapter()
    observation = adapter.observe()
    assert adapter.fresh(observation, candidate("c1")) is Freshness.FRESH
    assert adapter.fresh(observation, candidate("gone")) is Freshness.STALE


def test_act_on_a_stale_candidate_raises_scope_violation_not_a_retry():
    adapter = FakeAdapter()
    observation = adapter.observe()
    with pytest.raises(ScopeViolation):
        adapter.act(candidate("gone"), observation)


def test_mutation_result_names_its_input_method():
    adapter = FakeAdapter()
    result = adapter.act(candidate(), adapter.observe())
    assert result.input_method in {"pattern", "synthetic_key"}
    assert result.status == "confirmed"


def test_close_semantics_differ_by_ownership():
    owned = FakeAdapter(ownership="owned")
    attached = FakeAdapter(ownership="attached")
    owned.close()
    attached.close()
    assert owned.closed_as == "owned"
    assert attached.closed_as == "attached"


class FakeContextMenuAdapter:
    """Simulates the two-stage observe/act/observe contract for context menus (Phase A).

    Scope is judged against an owning-window tree, not one literal HWND: `menu_item` belongs to
    a popup owned by the same top-level window as `root`, so it is in scope once the menu has
    been opened; `other_window_item` belongs to an unrelated window and never is.
    """

    ownership = "attached"

    def __init__(self):
        self._root = candidate(id="root", operation=OPEN_CONTEXT_MENU, label="File")
        self._menu_item = candidate(id="menu_item", operation="click", label="Save As...")
        self._other_window_item = candidate(id="other_window_item", operation="click", label="Unrelated")
        self._menu_open = False

    def observe(self) -> Observation:
        candidates = (self._root, self._menu_item) if self._menu_open else (self._root,)
        return Observation(observation_id="obs-ctx", semantic_id="sem-ctx", candidates=candidates, complete=True)

    def _in_scope(self, candidate) -> bool:
        return candidate.id in {self._root.id, self._menu_item.id}

    def fresh(self, observation, candidate=None):
        if candidate is None:
            return Freshness.FRESH
        return Freshness.FRESH if self._in_scope(candidate) else Freshness.STALE

    def act(self, candidate, observation, text=None):
        if not self._in_scope(candidate):
            raise ScopeViolation("candidate is outside the owning-window tree")
        if candidate.id == self._root.id:
            self._menu_open = True
            return MutationResult(candidate.id, "synthetic_pointer", "confirmed")
        return MutationResult(candidate.id, "pattern", "confirmed")

    def close(self):
        pass


def test_opening_a_context_menu_is_an_ordinary_mutation_and_reveals_new_candidates_on_reobserve():
    adapter = FakeContextMenuAdapter()
    first = adapter.observe()
    assert [c.id for c in first.candidates] == ["root"]

    result = adapter.act(candidate("root", operation=OPEN_CONTEXT_MENU), first)
    assert result.input_method == "synthetic_pointer"
    assert result.status == "confirmed"

    second = adapter.observe()
    assert {c.id for c in second.candidates} == {"root", "menu_item"}
    menu_item = next(c for c in second.candidates if c.id == "menu_item")
    assert menu_item.operation == "click"


def test_menu_item_from_the_owning_popup_is_in_scope_once_the_menu_is_open():
    adapter = FakeContextMenuAdapter()
    adapter.act(candidate("root", operation=OPEN_CONTEXT_MENU), adapter.observe())
    observation = adapter.observe()
    menu_item = next(c for c in observation.candidates if c.id == "menu_item")
    result = adapter.act(menu_item, observation)
    assert result.status == "confirmed"


def test_candidate_from_an_unrelated_window_still_raises_scope_violation():
    adapter = FakeContextMenuAdapter()
    adapter.act(candidate("root", operation=OPEN_CONTEXT_MENU), adapter.observe())
    observation = adapter.observe()
    with pytest.raises(ScopeViolation):
        adapter.act(candidate("other_window_item"), observation)
