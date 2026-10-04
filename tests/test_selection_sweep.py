from dataclasses import replace

from finitact.action_adapter import Freshness, MutationResult, Observation
from finitact.contracts import Decision, ObservedCandidate
from finitact.selection_sweep import SelectionSweepCoordinator, sweep_selection_group


def candidate(identity, label, *, group="themes", selected=False, automation_id=None):
    return ObservedCandidate(
        identity,
        "select",
        label,
        identity,
        {
            "selection_group": group,
            "selection_is_selected": selected,
            "automation_id": automation_id if automation_id is not None else identity,
        },
    )


def observation(*candidates):
    return Observation("raw", "semantic", tuple(candidates), True)


class Adapter:
    ownership = "attached"

    def __init__(self, observations, statuses=None):
        self.observations = iter(observations)
        self.statuses = iter(statuses or ())
        self.acted = []

    def observe(self):
        return next(self.observations)

    def fresh(self, current, wanted=None):
        return Freshness.FRESH

    def act(self, wanted, current, text=None):
        self.acted.append(wanted.label)
        return MutationResult(wanted.id, "pattern", next(self.statuses, "confirmed"))

    def close(self):
        pass


class Provider:
    def __init__(self, choice):
        self.choice = choice
        self.requests = []

    def decide(self, request, *, attempts, call_id):
        self.requests.append(request)
        attempts.append({"status": "confirmed", "call_id": call_id})
        return Decision(choice=self.choice)


def test_sweep_freezes_group_order_rebinds_members_and_collects_evidence():
    first = observation(candidate("a", "Light"), candidate("b", "Dark"), candidate("x", "Explorer", group="tabs"))
    after_light = observation(candidate("a2", "Light", automation_id="a"), candidate("b2", "Dark", automation_id="b"))
    after_dark = observation(candidate("a3", "Light", automation_id="a"), candidate("b3", "Dark", automation_id="b"))
    adapter = Adapter([after_light, after_dark])

    result = sweep_selection_group(
        adapter, first, first.candidates[0], capture=lambda item, current: {"semantic": current.semantic_id}
    )

    assert result.completed
    assert adapter.acted == ["Light", "Dark"]
    assert [step.label for step in result.steps] == ["Light", "Dark"]
    assert all(step.status == "confirmed" for step in result.steps)


def test_sweep_stops_without_retry_after_uncertain_mutation():
    first = observation(candidate("a", "Light"), candidate("b", "Dark"))
    adapter = Adapter([], statuses=["uncertain"])
    result = sweep_selection_group(adapter, first, first.candidates[0], capture=lambda *_: {})
    assert not result.completed
    assert adapter.acted == ["Light"]
    assert result.steps[0].status == "uncertain"


def test_sweep_can_reprepare_a_transient_group_before_each_value():
    first = observation(candidate("a", "Light"), candidate("b", "Dark"))
    prepared = iter(
        [
            observation(candidate("a2", "Light", group="new-1"), candidate("b2", "Dark", group="new-1")),
            observation(candidate("a3", "Light", group="new-2"), candidate("b3", "Dark", group="new-2")),
        ]
    )
    after = iter([observation(), observation()])
    adapter = Adapter(after)
    result = sweep_selection_group(
        adapter, first, first.candidates[0], capture=lambda *_: {}, prepare=lambda: next(prepared)
    )
    assert result.completed
    assert adapter.acted == ["Light", "Dark"]


def test_sweep_rejects_candidate_without_group_membership():
    seed = replace(candidate("a", "Light"), attributes={"automation_id": "a"})
    try:
        sweep_selection_group(Adapter([]), observation(seed), seed, capture=lambda *_: {})
    except ValueError as exc:
        assert "selection_group" in str(exc)
    else:
        raise AssertionError("ungrouped candidate was accepted")


def test_coordinator_uses_provider_only_to_choose_group_then_sweeps_every_member():
    themes = [candidate("light", "Light"), candidate("dark", "Dark")]
    tabs = [candidate("files", "Files", group="tabs"), candidate("search", "Search", group="tabs")]
    first = observation(*themes, *tabs)
    adapter = Adapter([first, first])
    provider = Provider("dark")
    result = SelectionSweepCoordinator(provider).execute(
        adapter, first, "Sweep every theme", capture=lambda *_: {}
    )
    assert result.completed
    assert adapter.acted == ["Light", "Dark"]
    assert [candidate.label for candidate in provider.requests[0].candidates] == [
        "Light",
        "Dark",
        "Files",
        "Search",
    ]


def test_coordinator_rejects_terminal_provider_decision_before_mutation():
    first = observation(candidate("light", "Light"), candidate("dark", "Dark"))

    class TerminalProvider(Provider):
        def decide(self, request, *, attempts, call_id):
            return Decision(terminal_reason="blocked")

    adapter = Adapter([])
    try:
        SelectionSweepCoordinator(TerminalProvider(None)).execute(
            adapter, first, "Sweep every theme", capture=lambda *_: {}
        )
    except ValueError as exc:
        assert "did not select" in str(exc)
    else:
        raise AssertionError("terminal decision started a sweep")
    assert adapter.acted == []
