"""EXP-0015: ADR-0017 entry points — app annotations on observed candidates and an app-published state predicate."""

import json

import pytest

from finitact.action_adapter import Freshness, MutationResult, Observation
from finitact.contracts import Decision, ObservedCandidate
from finitact.mcp_server import WindowsRunInput
from finitact.runs import AppAnnotation, AppExpect, Goal, WindowsRunCoordinator, WindowsRunRequest


class Provider:
    def __init__(self, *choices):
        self.choices = iter(choices)
        self.requests = []

    def decide(self, request, *, attempts, call_id):
        self.requests.append(request)
        attempts.append({"status": "confirmed", "call_id": call_id})
        choice = next(self.choices)
        return Decision(terminal_reason=choice.lower()) if choice in {"DONE", "BLOCKED"} else Decision(choice=choice)


class Adapter:
    def __init__(self, labels, on_act=None, items=None):
        self.labels = labels
        self.items = items
        self.semantic = 1
        self.on_act = on_act
        self.acts = []

    def observe(self):
        items = self.items or tuple(
            ObservedCandidate(f"c{i}", "click", label, f"s{i}", {"rect": [0, 20 * i, 80, 16]})
            for i, label in enumerate(self.labels)
        )
        return Observation(f"obs-{self.semantic}", f"sem-{self.semantic}", items, True)

    def fresh(self, observation, candidate=None):
        return Freshness.FRESH

    def act(self, candidate, observation, text=None):
        self.acts.append(candidate.id)
        self.semantic += 1
        if self.on_act:
            self.on_act(candidate)
        return MutationResult(candidate.id, "pattern", "confirmed")

    def close(self):
        pass


NOTE = AppAnnotation("16:9  aspect", "dropdown", "Game view resolution", "16:9 Aspect", "unity-editor")


def run(adapter, provider, **values):
    coordinator = WindowsRunCoordinator(adapter_factory=lambda _r: adapter, decision_provider=provider, **values.pop("loop", {}))
    coordinator._sleep = lambda _s: None
    request = WindowsRunRequest(
        run_id="r1", target_id="uia:1", goals=values.pop("goals", (Goal("g1", "Open the resolution dropdown"),)),
        allowed_operations=values.pop("allowed_operations", ("click",)), **values,
    )
    return coordinator.execute(request)


def test_annotation_reaches_the_provider_on_the_unique_match_of_the_run_start_only():
    provider = Provider("c1", "DONE")
    run(Adapter(["Game", "16:9 Aspect"]), provider, app_annotations=(NOTE,))

    first, second = provider.requests
    annotated = {item.id: item.attributes.get("app") for item in first.candidates}
    assert all(item.attributes["rect"] for item in first.candidates)
    assert annotated == {"c0": None, "c1": {"role": "dropdown", "name": "Game view resolution",
                                            "value": "16:9 Aspect", "source": "unity-editor"}}
    assert [item.label for item in first.candidates] == ["Game", "16:9 Aspect"]
    assert all("app" not in item.attributes for item in second.candidates)


def test_ambiguous_annotation_is_not_attached():
    provider = Provider("DONE")
    run(Adapter(["16:9 Aspect", "16:9 aspect"]), provider, app_annotations=(NOTE,))

    assert all("app" not in item.attributes for item in provider.requests[0].candidates)


def test_one_region_offering_click_and_fill_is_one_match():
    rect = {"rect": [459, 113, 113, 17]}
    items = (
        ObservedCandidate("c0", "click", "Game", "s0", {"rect": [0, 0, 40, 16]}),
        ObservedCandidate("c1", "click", "16:9 Aspect", "ocr_1", rect),
        ObservedCandidate("c2", "fill", "16:9 Aspect", "ocr_1", rect),
    )
    provider = Provider("DONE")
    run(Adapter([], items=items), provider, app_annotations=(NOTE,), allowed_operations=("click", "fill"))

    annotated = {item.id: "app" in item.attributes for item in provider.requests[0].candidates}
    assert annotated == {"c0": False, "c1": True, "c2": True}


def write_feed(path, t, **state):
    path.write_text(json.dumps({"pid": 1, "t": t, **state}), encoding="utf-8")


def test_app_state_written_after_the_action_verifies_the_goal(tmp_path):
    feed = tmp_path / "feed.json"
    now = [100.0]
    write_feed(feed, 99.0, workspace="Layout")
    adapter = Adapter(["Shading"], on_act=lambda _c: write_feed(feed, 100.5, workspace="Shading"))
    result = run(
        adapter, Provider("c0"),
        goals=(Goal("g1", "Switch to Shading", app_expect=AppExpect(str(feed), "workspace", "Shading")),),
        loop={"wall_clock": lambda: now[0]},
    )

    assert result.goals[0].termination_reason == "outcome_verified"
    assert result.goals[0].outcome == "verified_success"


@pytest.mark.parametrize("written", [(99.9, "Shading"), (100.5, "Layout"), None])
def test_stale_different_or_missing_app_state_gives_no_verdict(tmp_path, written):
    feed = tmp_path / "feed.json"
    ticks = iter(range(100, 200))

    def act(_c):
        if written:
            write_feed(feed, written[0], workspace=written[1])

    result = run(
        Adapter(["Shading"], on_act=act), Provider("c0", "DONE"),
        goals=(Goal("g1", "Switch to Shading", app_expect=AppExpect(str(feed), "workspace", "Shading")),),
        loop={"wall_clock": lambda: float(next(ticks))},
    )

    assert result.goals[0].termination_reason == "provider_done"
    assert result.goals[0].outcome == "unverified"


def test_mcp_input_carries_annotations_and_app_expect():
    contract = WindowsRunInput(
        target_id="window:1:2",
        goals=[{"goal": "Switch", "app_expect": {"source": "C:/feed.json", "key": "mode", "equals": "EDIT_MESH"}}],
        app_annotations=[{"match": {"text": "16:9 Aspect"}, "role": "dropdown", "name": "Game view resolution",
                          "value": "16:9 Aspect", "source": "unity-editor"}],
    ).contract()

    assert contract.goals[0].app_expect == AppExpect("C:/feed.json", "mode", "EDIT_MESH")
    assert contract.app_annotations[0].match_text == "16:9 Aspect"
