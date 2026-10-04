"""ADR-0046: drag on the browser path is opt-in, two-step, and delivered as mouse or replayed HTML5 drag events."""

import pytest

import finitact.browser as browser
from finitact.action_adapter import ScopeViolation
from finitact.browser import StalePage
from finitact.browser_adapter import BrowserAdapter
from finitact.contracts import Decision
from finitact.runs import BROWSER_OPERATIONS, Goal, RunCoordinator, RunRequest, WindowsRunCoordinator

ORIGIN = "https://example.test"
RECT = {"x": 0, "y": 0, "w": 10, "h": 10}


def page(fingerprint="p1", **extra):
    return {
        "fingerprint": fingerprint, "semantic_fingerprint": "s" + fingerprint, "url": f"{ORIGIN}/a", "title": "t",
        "text": "x", "marker": ["m"],
        "actions": [{"id": "e1", "kind": "click", "label": "Trash", "node": 7, "rect": RECT}],
        "drag_sources": [{"node": 1, "label": "Card A", "rect": RECT}, {"node": 2, "label": "Card B", "rect": RECT}],
        "drop_targets": [{"node": 3, "label": "Done", "rect": RECT}],
        **extra,
    }  # fmt: skip


class Browser:
    def __init__(self, *pages):
        self.pages, self.acted = list(pages), []

    def observe(self, screenshot=True):
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def fresh(self, page, action=None):
        return True

    def act(self, action, page, text=None):
        self.acted.append(action)

    def close(self):
        pass


def test_drag_candidates_exist_only_when_the_run_asked_for_drag():
    plain = BrowserAdapter(Browser(page()), allowed_origins=[ORIGIN]).observe()
    assert [c.operation for c in plain.candidates] == ["click"]
    observation = BrowserAdapter(Browser(page()), allowed_origins=[ORIGIN], drag=True).observe()
    starts = [c for c in observation.candidates if c.operation == "drag"]
    assert [(c.label, c.attributes["drag_phase"]) for c in starts] == [
        ("Choose drag start at 'Card A'", "start"), ("Choose drag start at 'Card B'", "start"),
    ]  # fmt: skip


def test_choosing_a_start_offers_ends_in_order_without_the_start_itself():
    adapter = BrowserAdapter(Browser(page()), allowed_origins=[ORIGIN], drag=True)
    observation = adapter.observe()
    start = next(c for c in observation.candidates if c.label.endswith("'Card A'"))
    ends = adapter.drag_end_observation(start, observation)
    assert [c.label for c in ends.candidates] == [
        "Drag from 'Card A' to 'Done'", "Drag from 'Card A' to 'Card B'", "Drag from 'Card A' to 'Trash'",
    ]  # fmt: skip
    assert {c.attributes["drag_phase"] for c in ends.candidates} == {"end"}
    assert ends.observation_id != observation.observation_id
    assert adapter.page["fingerprint"] == "p1"  # final state stays the page, not the ends


def test_the_end_carries_its_start_node_to_delivery():
    browser_ = Browser(page())
    adapter = BrowserAdapter(browser_, allowed_origins=[ORIGIN], drag=True)
    observation = adapter.observe()
    start = next(c for c in observation.candidates if c.operation == "drag")
    ends = adapter.drag_end_observation(start, observation)
    assert adapter.act(ends.candidates[0], ends).status == "confirmed"
    assert (browser_.acted[0]["from_node"], browser_.acted[0]["node"]) == (1, 3)


def test_only_an_offered_start_can_open_the_ends():
    adapter = BrowserAdapter(Browser(page()), allowed_origins=[ORIGIN], drag=True)
    observation = adapter.observe()
    with pytest.raises(ScopeViolation):
        adapter.drag_end_observation(observation.candidates[0], observation)  # a click
    plain = BrowserAdapter(Browser(page()), allowed_origins=[ORIGIN])
    seen = plain.observe()
    with pytest.raises(ScopeViolation):
        plain.drag_end_observation(seen.candidates[0], seen)


class Provider:
    def __init__(self, *choices):
        self.choices = iter(choices)
        self.requests = []

    def decide(self, request, *, attempts, call_id):
        self.requests.append(request)
        attempts.append({"status": "confirmed"})
        choice = next(self.choices)
        return Decision(terminal_reason=choice.lower()) if choice in {"DONE", "BLOCKED"} else Decision(choice=choice)


def test_the_shared_loop_takes_start_then_end_and_counts_one_mutation():
    browser_ = Browser(page("p1"), page("p2"))
    provider = Provider("d1", "d1>1", "DONE")

    def shared(request):
        return WindowsRunCoordinator(
            adapter_factory=lambda _r: BrowserAdapter(browser_, allowed_origins=request.allowed_origins, drag=True),
            decision_provider=provider,
        )

    result = RunCoordinator(shared_loop=shared).execute(
        RunRequest("b1", f"{ORIGIN}/a", (Goal("g1", "Move Card A to Done"),), (ORIGIN,), extra_operations=("drag",))
    )
    goal = result.goals[0]
    assert (goal.termination_reason, goal.mutation_state) == ("provider_done", "confirmed")
    assert [(a["from_node"], a["node"]) for a in browser_.acted] == [(1, 3)]
    assert len(provider.requests) == 3  # start choice, end choice, DONE


def test_extra_operations_are_validated_and_leave_old_fingerprints_alone():
    base = RunRequest("b1", ORIGIN, (Goal("g1", "x"),), (ORIGIN,))
    assert base.fingerprint() == RunRequest("b1", ORIGIN, (Goal("g1", "x"),), (ORIGIN,), extra_operations=()).fingerprint()
    assert base.fingerprint() != RunRequest("b1", ORIGIN, (Goal("g1", "x"),), (ORIGIN,), extra_operations=("drag",)).fingerprint()
    assert "drag" not in BROWSER_OPERATIONS
    with pytest.raises(ValueError, match="unknown extra operation"):
        RunRequest("b1", ORIGIN, (Goal("g1", "x"),), (ORIGIN,), extra_operations=("fly",))
    with pytest.raises(ValueError, match="both extra and denied"):
        RunRequest("b1", ORIGIN, (Goal("g1", "x"),), (ORIGIN,), extra_operations=("drag",), denied_operations=("drag",))


class Cdp:
    """Records CDP calls; ``intercept`` makes the browser report an HTML5 drag takeover on the first move."""

    def __init__(self, points, intercept=False):
        self.points, self.intercept, self.calls, self.events = points, intercept, [], []

    def __call__(self, method, session_id=None, **params):
        self.calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"value": self.points}}
        if method == "Input.dispatchMouseEvent" and params["type"] == "mouseMoved" and params["buttons"] and self.intercept:
            self.events.append({"method": "Input.dragIntercepted", "params": {"data": {"items": []}}})
        return {}

    def drain(self):
        events, self.events = self.events, []
        return events


ACTION = {"id": "d1>1", "kind": "drag", "node": 3, "from_node": 1}
POINTS = {"from": {"x": 10.0, "y": 10.0}, "to": {"x": 110.0, "y": 60.0}}


def run_act(monkeypatch, cdp):
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser, "drain_events", cdp.drain)
    monkeypatch.setattr(browser, "DRAG_STEP_S", 0)
    monkeypatch.setattr(browser, "INTERCEPT_WAIT_S", 0)
    return browser.browser_operation({"operation": "act", "session": "s", "action": ACTION})


def test_mouse_drag_presses_moves_stepwise_and_releases_at_the_end(monkeypatch):
    cdp = Cdp(POINTS)
    assert run_act(monkeypatch, cdp)["executed"] == "d1>1"
    mouse = [p for m, p in cdp.calls if m == "Input.dispatchMouseEvent"]
    assert [p["type"] for p in mouse] == [
        "mouseMoved", "mousePressed", *["mouseMoved"] * (browser.DRAG_STEPS + browser.DWELL_MOVES), "mouseReleased",
    ]  # the dwell keeps the pointer on the end long enough for libraries that decide on a timer
    assert (mouse[1]["x"], mouse[1]["y"]) == (10.0, 10.0) and (mouse[-1]["x"], mouse[-1]["y"]) == (110.0, 60.0)
    assert not any(m == "Input.dispatchDragEvent" for m, _ in cdp.calls)
    assert cdp.calls[-1] == ("Input.setInterceptDrags", {"enabled": False})


def test_html5_drag_is_replayed_as_drag_events_at_the_end(monkeypatch):
    cdp = Cdp(POINTS, intercept=True)
    run_act(monkeypatch, cdp)
    drags = [(p["type"], p["x"], p["y"]) for m, p in cdp.calls if m == "Input.dispatchDragEvent"]
    # The browser's remaining path is replayed as over events (Sortable decides from where the pointer came),
    # then the end is held before the drop.
    assert [d[0] for d in drags] == ["dragEnter", *["dragOver"] * (2 * (browser.DRAG_STEPS + browser.DWELL_MOVES) - 1), "drop"]
    assert drags[0][1:] == (20.0, 15.0) and drags[-2][1:] == drags[-1][1:] == (110.0, 60.0)
    moves = [p for m, p in cdp.calls if m == "Input.dispatchMouseEvent" and p["type"] == "mouseMoved"]
    assert len(moves) == 2  # the start position, then the one move the browser took over


def test_a_covered_or_moved_end_is_stale_before_any_input(monkeypatch):
    cdp = Cdp(None)
    with pytest.raises(StalePage):
        run_act(monkeypatch, cdp)
    assert not any(m == "Input.dispatchMouseEvent" for m, _ in cdp.calls)


def test_drag_events_not_its_own_are_handed_back_for_download_tracking(monkeypatch):
    cdp = Cdp(POINTS)
    cdp.events.append({"method": "Browser.downloadWillBegin", "params": {"guid": "g"}})
    assert run_act(monkeypatch, cdp)["events"] == [{"method": "Browser.downloadWillBegin", "params": {"guid": "g"}}]
