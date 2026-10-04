import pytest

from finitact.action_adapter import Freshness, MutationUncertain, ScopeViolation
from finitact.browser import MutationUncertain as BrowserMutationUncertain
from finitact.browser import StalePage
from finitact.browser_adapter import BrowserAdapter


def page(fingerprint="p1", url="https://example.test/a", semantic="s1"):
    return {
        "fingerprint": fingerprint,
        "semantic_fingerprint": semantic,
        "url": url,
        "title": "Example",
        "text": "hello",
        "actions": [{"id": "a1", "kind": "click", "label": "Save", "node": 3, "rect": [0, 0, 1, 1]}],
    }


class FakeBrowser:
    def __init__(self, *pages, fresh=True, act_error=None):
        self.pages = iter(pages)
        self.is_fresh = fresh
        self.act_error = act_error
        self.acted = []
        self.closed = False

    def observe(self, screenshot=True):
        return next(self.pages)

    def fresh(self, page, action=None):
        return self.is_fresh

    def act(self, action, page, text=None):
        if self.act_error:
            raise self.act_error
        self.acted.append((action["id"], text))

    def close(self):
        self.closed = True


def test_dom_actions_become_observed_candidates_and_a_click_is_confirmed():
    browser = FakeBrowser(page())
    adapter = BrowserAdapter(browser, allowed_origins=["https://example.test"])
    observation = adapter.observe()
    assert (observation.observation_id, observation.semantic_id) == ("p1", "s1")
    candidate = observation.candidates[0]
    assert (candidate.id, candidate.operation, candidate.label) == ("a1", "click", "Save")
    assert adapter.fresh(observation, candidate) is Freshness.FRESH
    assert adapter.act(candidate, observation).status == "confirmed"
    assert browser.acted == [("a1", None)]


def test_stale_before_input_is_not_attempted_but_an_unknown_select_result_is_uncertain():
    """ADR-0037: the three delivery states stay distinct (consult 20260925-2218 point 1)."""
    stale = BrowserAdapter(
        FakeBrowser(page(), act_error=StalePage("changed")), allowed_origins=["https://example.test"]
    )
    observation = stale.observe()
    result = stale.act(observation.candidates[0], observation)
    assert result.status == "not_attempted" and result.detail.startswith("stale_before_input")
    unknown = BrowserAdapter(
        FakeBrowser(page(), act_error=BrowserMutationUncertain("lost")), allowed_origins=["https://example.test"]
    )
    observation = unknown.observe()
    with pytest.raises(MutationUncertain):
        unknown.act(observation.candidates[0], observation)


def test_origin_gate_and_browser_request_shape_are_kept():
    adapter = BrowserAdapter(FakeBrowser(page(url="https://other.test/")), allowed_origins=["https://example.test"])
    observation = adapter.observe()
    with pytest.raises(ScopeViolation):
        adapter.check_scope(observation)
    request = adapter.decision_request(observation, "Save it", [], 5)
    assert (
        "control_values" not in request.untrusted_context and request.untrusted_context["url"] == "https://other.test/"
    )


def test_provider_input_matches_the_agent_loop_for_the_same_page():
    """ADR-0037: moving the loop must not change what Jev receives (consult 20260925-2218 point 5)."""
    from finitact.contracts import decision_request

    observed = page()
    adapter = BrowserAdapter(FakeBrowser(observed), allowed_origins=["https://example.test"])
    history = [{"action": "Open", "kind": "click", "text": None}]
    assert adapter.decision_request(adapter.observe(), "Save it", history, 7) == decision_request(
        observed, "Save it", history, 7
    )


def _toggle_page(fingerprint, checked, role="checkbox", extra=()):
    return {
        "fingerprint": fingerprint, "semantic_fingerprint": fingerprint, "url": "https://example.test/a", "text": "",
        "actions": [{"id": "e1", "kind": "click", "label": "VPN Connection", "node": 5, "role": role, "checked": checked}, *extra],
    }  # fmt: skip


def test_effect_is_judged_against_the_expected_state_not_against_any_change():
    """ADR-0039 (codex research): a checkbox flips, a radio is selected, a select takes the chosen option."""
    reverted = BrowserAdapter(FakeBrowser(_toggle_page("p1", "true"), _toggle_page("p2", "true")), allowed_origins=["https://example.test"])
    before, after = reverted.observe(), reverted.observe()
    effect = reverted.effect(before.candidates[0], before, after, None)
    assert effect == {"target": "VPN Connection", "expected": {"checked": "false"}, "before": "true", "after": "true", "effect": "not_met"}
    radio = BrowserAdapter(FakeBrowser(_toggle_page("p1", "true", "radio"), _toggle_page("p2", "true", "radio")), allowed_origins=["https://example.test"])
    before, after = radio.observe(), radio.observe()
    assert radio.effect(before.candidates[0], before, after, None)["effect"] == "met"
    option = {"id": "e2", "kind": "select", "label": "Unit → millions", "node": 7, "option": "millions", "current_value": "thousands"}
    chosen = {**option, "id": "e2", "label": "Unit → thousands", "option": "thousands", "current_value": "millions"}
    select = BrowserAdapter(FakeBrowser(_toggle_page("p1", "true", extra=(option,)), _toggle_page("p2", "true", extra=(chosen,))), allowed_origins=["https://example.test"])
    before, after = select.observe(), select.observe()
    assert select.effect(before.candidates[1], before, after, None)["effect"] == "met"


def _dropdown_page(fingerprint, trigger_label, expanded, extra_actions=()):
    return {
        "fingerprint": fingerprint, "semantic_fingerprint": fingerprint, "url": "https://example.test/a", "text": "",
        "actions": [{"id": "t1", "kind": "click", "label": trigger_label, "node": 10, "role": "button",
                     "expanded": expanded}, *extra_actions],
    }  # fmt: skip


def test_a_listbox_option_click_is_judged_by_its_trigger_once_the_list_closes():
    """BUG-0023: a custom (non-<select>) dropdown removes its options when it closes, so the option's own
    node has no match in ``after``; the effect is read from the trigger that had it open instead."""
    option = {"id": "o1", "kind": "click", "label": "Million requests", "node": 20, "role": "option"}
    before_page = _dropdown_page("p1", "Requests unit: Thousand requests", "true", (option,))
    after_page = _dropdown_page("p2", "Requests unit: Million requests", None)
    adapter = BrowserAdapter(FakeBrowser(before_page, after_page), allowed_origins=["https://example.test"])
    before, after = adapter.observe(), adapter.observe()
    candidate = next(c for c in before.candidates if c.label == "Million requests")
    assert adapter.effect(candidate, before, after, None)["effect"] == "met"


def test_a_listbox_option_click_whose_trigger_stayed_open_is_not_met():
    option = {"id": "o1", "kind": "click", "label": "Million requests", "node": 20, "role": "option"}
    before_page = _dropdown_page("p1", "Requests unit: Thousand requests", "true", (option,))
    after_page = _dropdown_page("p2", "Requests unit: Thousand requests", "true", (option,))
    adapter = BrowserAdapter(FakeBrowser(before_page, after_page), allowed_origins=["https://example.test"])
    before, after = adapter.observe(), adapter.observe()
    candidate = next(c for c in before.candidates if c.label == "Million requests")
    assert adapter.effect(candidate, before, after, None)["effect"] == "not_met"


def test_a_listbox_option_click_with_an_ambiguous_number_of_open_triggers_is_unjudged():
    option = {"id": "o1", "kind": "click", "label": "Million requests", "node": 20, "role": "option"}
    before_page = {
        "fingerprint": "p1", "semantic_fingerprint": "p1", "url": "https://example.test/a", "text": "",
        "actions": [
            {"id": "t1", "kind": "click", "label": "A", "node": 10, "role": "button", "expanded": "true"},
            {"id": "t2", "kind": "click", "label": "B", "node": 11, "role": "button", "expanded": "true"},
            option,
        ],
    }
    after_page = {**before_page, "fingerprint": "p2", "semantic_fingerprint": "p2"}
    adapter = BrowserAdapter(FakeBrowser(before_page, after_page), allowed_origins=["https://example.test"])
    before, after = adapter.observe(), adapter.observe()
    candidate = next(c for c in before.candidates if c.label == "Million requests")
    assert adapter.effect(candidate, before, after, None) is None


def test_a_reformatted_fill_is_unknown_and_an_emptied_one_is_not_met():
    field = {"id": "e1", "kind": "fill", "label": "Requests", "node": 3, "value": ""}
    cases = {"42,500": "unknown", "": "not_met", "42500": "met"}
    for after_value, expected in cases.items():
        page_after = {**_toggle_page("p2", None), "actions": [{**field, "value": after_value}]}
        adapter = BrowserAdapter(FakeBrowser({**_toggle_page("p1", None), "actions": [field]}, page_after), allowed_origins=["https://example.test"])
        before, after = adapter.observe(), adapter.observe()
        assert adapter.effect(before.candidates[0], before, after, "42500")["effect"] == expected
