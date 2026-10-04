"""ADR-0047: per-session dialog state, pending delivery and resuming the input a dialog interrupted."""

import threading

import pytest

import finitact.browser as browser
from finitact.browser import MutationUncertain, StalePage


class Tab:
    """A fake CDP tab whose ``blocking`` input opens a dialog and answers only once the dialog is handled."""

    def __init__(self, blocking="mouseReleased", focused=True, session="s", target="t"):
        self.blocking, self.focused, self.session, self.target = blocking, focused, session, target
        self.calls, self.events, self.closed = [], [], threading.Event()

    def open(self, session=None, message="Save?"):
        self.events.append({"method": "Page.javascriptDialogOpening", "session_id": session or self.session,
                            "params": {"type": "confirm", "message": message, "frameId": self.target}})

    def __call__(self, method, session_id=None, _response_timeout=None, **params):
        self.calls.append((method, params))
        if method == "Page.handleJavaScriptDialog":
            self.closed.set()
            return {}
        if method == "Runtime.evaluate":
            if "activeElement" in params["expression"]:
                return {"result": {"value": self.focused}}
            return {"result": {"value": {"x": 5, "y": 5}}}
        if params.get("type") == self.blocking:
            self.closed.clear()
            self.open()
            assert self.closed.wait(5)
        return {}

    def drain(self):
        events, self.events = self.events, []
        return events


@pytest.fixture
def tab(monkeypatch):
    fake = Tab()
    monkeypatch.setattr(browser, "cdp", fake)
    monkeypatch.setattr(browser, "drain_events", fake.drain)
    monkeypatch.setattr(browser, "_send", lambda request: {"dialog": None})
    monkeypatch.setattr(browser, "SESSIONS", {})
    monkeypatch.setattr(browser, "BACKLOG", [])
    monkeypatch.setattr(browser, "DIALOG_POLL_S", 0.01)
    browser.session_state(fake.session, fake.target)
    return fake


def act(tab, action, text=None):
    return browser.browser_operation(
        {"operation": "act", "session": tab.session, "target": tab.target, "action": action, "text": text})


def answer(tab, choice="accept"):
    return act(tab, {"id": f"dialog_{choice}", "kind": "click", "dialog": choice})


def test_a_dialog_during_a_click_leaves_it_pending_until_the_answer(tab):
    result = act(tab, {"id": "e1", "kind": "click", "node": 1})
    assert result["pending"] and result["dialog"]["message"] == "Save?"
    assert browser.SESSIONS["s"].pending is not None
    assert answer(tab) == {"executed": "dialog_accept", "resolved": "e1"}
    assert browser.SESSIONS["s"].pending is None


def test_a_fill_interrupted_by_its_click_types_its_text_after_the_answer(tab):
    act(tab, {"id": "e2", "kind": "fill", "node": 2}, "Tokyo")
    assert not any(m == "Input.insertText" for m, _ in tab.calls)
    assert answer(tab)["resolved"] == "e2"
    assert tab.calls[-1] == ("Input.insertText", {"text": "Tokyo"})


def test_a_fill_that_lost_its_field_is_uncertain_not_typed_elsewhere(tab):
    tab.focused = False
    act(tab, {"id": "e2", "kind": "fill", "node": 2}, "Tokyo")
    with pytest.raises(MutationUncertain, match="lost its field"):
        answer(tab)
    assert not any(m == "Input.insertText" for m, _ in tab.calls)


def test_no_other_input_is_sent_while_one_is_pending(tab):
    act(tab, {"id": "e1", "kind": "click", "node": 1})
    with pytest.raises(MutationUncertain, match="unfinished"):
        act(tab, {"id": "e3", "kind": "click", "node": 3})
    tab.closed.set()


def test_a_dialog_before_any_input_is_stale_not_pending(tab):
    tab.blocking = None
    tab.open()
    with pytest.raises(StalePage):
        # The target lookup (read-only) is the call the open dialog blocks.
        browser.browser_operation({"operation": "act", "session": "s", "target": "t",
                                   "action": {"id": "e1", "kind": "scroll", "node": 1, "delta": 100}})
    assert browser.SESSIONS["s"].pending is None


def test_another_tabs_dialog_neither_shows_here_nor_clears_ours(tab):
    browser.session_state("other", "t2")
    tab.open(session="other")
    assert browser.current_dialog("s", "t") is None
    tab.open(message="Ours")
    tab.events.append({"method": "Page.javascriptDialogClosed", "session_id": "other", "params": {}})
    assert browser.current_dialog("s", "t")[1]["message"] == "Ours"


def test_same_text_dialogs_are_told_apart_by_generation(tab):
    tab.open()
    first = browser.dialog_page(browser.current_dialog("s", "t"), "http://x", "x")
    tab.events.append({"method": "Page.javascriptDialogClosed", "session_id": "s", "params": {}})
    tab.open()
    second = browser.dialog_page(browser.current_dialog("s", "t"), "http://x", "x")
    assert first["fingerprint"] != second["fingerprint"]
    assert first["semantic_fingerprint"] == second["semantic_fingerprint"]
    b = browser.Browser.__new__(browser.Browser)
    b.session, b.target = "s", "t"
    assert not b.fresh(first) and b.fresh(second)


def test_prompt_fill_says_it_submits_at_once():
    page = browser.dialog_page((1, {"type": "prompt", "message": "Name?", "defaultPrompt": "a"}), "u", "t")
    fill = page["actions"][0]
    assert fill["kind"] == "fill" and "submits at once" in fill["label"] and fill["value"] == "a"


def test_the_provider_sees_each_dialog_choice_as_its_own_target():
    from finitact import model

    page = browser.dialog_page((1, {"type": "prompt", "message": "Name?"}), "u", "t")
    elements, targets, _ = model.action_space(page["actions"])
    assert [e["label"] for e in elements] == [a["label"] for a in page["actions"]]
    assert {a["id"] for a in targets["CLICK"].values()} == {"dialog_dismiss"}
    assert [a["id"] for a in targets["TYPE_TEXT"].values()] == ["dialog_prompt"]
