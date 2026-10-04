from finitact.action_adapter import Freshness, MutationResult, Observation
from finitact.contracts import Decision, ObservedCandidate
from finitact.runs import Goal, WindowsRunCoordinator, WindowsRunRequest
from finitact.windows_outcome_verifier import (
    OwnedWindowDiff,
    WindowsOwnedWindowVerifier,
    class_is,
    by_goal_id,
    first_verdict,
    title_is,
    window_appeared,
    window_disappeared,
)
from finitact.windows_screen_grounded import OwnedWindow


class FakeEnumerator:
    def __init__(self, *snapshots):
        self.snapshots = iter(snapshots)
        self.calls = 0

    def list_visible(self, process_id):
        self.calls += 1
        return next(self.snapshots)


MAIN = OwnedWindow(100, "MainWndClass", "Main")
POPUP = OwnedWindow(200, "PopupWndClass", "")
OBS = Observation("obs", "sem", (), True)


def test_verifier_diffs_against_the_baseline_captured_when_the_goal_began():
    enumerator = FakeEnumerator((MAIN,), (MAIN, POPUP), (MAIN, POPUP))
    seen = []
    verifier = WindowsOwnedWindowVerifier(
        enumerator, process_id=7, judge=lambda goal, observation, diff: seen.append(diff) or None
    )
    assert enumerator.calls == 0  # a server-start snapshot would be stale by the time a run arrives

    goal = Goal("g1", "open the popup")
    observation = Observation("obs", "sem", (), True)
    verifier.begin_goal(goal)
    verifier(goal, observation)
    verifier(goal, observation)

    assert seen[0] == OwnedWindowDiff(appeared=(POPUP,), disappeared=())
    assert seen[0].changed is True
    # second call diffs against the same goal-start baseline, not the previous call's snapshot
    assert seen[1] == OwnedWindowDiff(appeared=(POPUP,), disappeared=())


def test_begin_goal_rebaselines_so_a_second_goal_sees_what_it_closed():
    enumerator = FakeEnumerator((MAIN,), (MAIN, POPUP), (MAIN, POPUP), (MAIN,))
    verifier = WindowsOwnedWindowVerifier(enumerator, process_id=7, judge=lambda goal, observation, diff: diff)
    verifier.begin_goal(Goal("g1", "open"))
    assert verifier(Goal("g1", "open"), OBS) == OwnedWindowDiff((POPUP,), ())
    verifier.begin_goal(Goal("g2", "close"))
    assert verifier(Goal("g2", "close"), OBS) == OwnedWindowDiff((), (POPUP,))


def test_verifying_without_a_goal_baseline_raises_instead_of_guessing():
    verifier = WindowsOwnedWindowVerifier(FakeEnumerator(), process_id=7, judge=lambda *_: True)
    try:
        verifier(Goal("g1", "open"), OBS)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")


def test_diff_reports_no_change_when_the_owned_window_set_is_unchanged():
    enumerator = FakeEnumerator((MAIN,), (MAIN,))
    verifier = WindowsOwnedWindowVerifier(
        enumerator, process_id=7, judge=lambda goal, observation, diff: diff.changed
    )
    verifier.begin_goal(Goal("g1", "no-op"))
    result = verifier(Goal("g1", "no-op"), Observation("obs", "sem", (), True))
    assert result is False


def test_verifier_never_invents_a_verdict_the_judge_did_not_give():
    enumerator = FakeEnumerator((MAIN,), (MAIN, POPUP))
    verifier = WindowsOwnedWindowVerifier(enumerator, process_id=7, judge=lambda *_: None)
    verifier.begin_goal(Goal("g1", "open the popup"))
    assert verifier(Goal("g1", "open the popup"), Observation("obs", "sem", (), True)) is None


class ScriptedProvider:
    def __init__(self, *choices):
        self.choices = iter(choices)

    def decide(self, request, *, attempts, call_id):
        attempts.append({"status": "confirmed", "call_id": call_id})
        choice = next(self.choices)
        return Decision(terminal_reason=choice.lower()) if choice in {"DONE", "BLOCKED"} else Decision(choice=choice)


class FakeAdapter:
    ownership = "attached"

    def __init__(self):
        self.item = ObservedCandidate("open", "click", "Open resolution popup", "runtime-1")
        self.semantic = 1

    def observe(self):
        return Observation(f"obs-{self.semantic}", f"sem-{self.semantic}", (self.item,), True)

    def fresh(self, observation, candidate=None):
        return Freshness.FRESH

    def act(self, candidate, observation, text=None):
        self.semantic += 1
        return MutationResult(candidate.id, "pattern", "confirmed")

    def close(self):
        pass


def test_windows_run_coordinator_treats_an_appeared_owned_popup_as_verified_success():
    # goal start: baseline, then the pre-action check; after the click: the popup is there
    enumerator = FakeEnumerator((MAIN,), (MAIN,), (MAIN, POPUP))
    verifier = WindowsOwnedWindowVerifier(
        enumerator, process_id=7, judge=lambda goal, observation, diff: diff.changed or None
    )
    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(),
        decision_provider=ScriptedProvider("open"),
        verifier=verifier,
    )
    result = coordinator.execute(
        WindowsRunRequest(
            run_id="w1",
            target_id="screen:100:7",
            goals=(Goal("g1", "open the resolution popup"),),
            allowed_operations=("click",),
        )
    )
    assert result.goals[0].termination_reason == "outcome_verified"
    assert result.goals[0].outcome == "verified_success"


ERROR = OwnedWindow(300, "#32770", "Error")
GOAL = Goal("g1", "open the popup")


def test_window_appeared_gives_no_verdict_until_the_matching_window_exists():
    judge = window_appeared(class_is("PopupWndClass"))
    assert judge(GOAL, OBS, OwnedWindowDiff((), ())) is None
    assert judge(GOAL, OBS, OwnedWindowDiff((ERROR,), ())) is None
    assert judge(GOAL, OBS, OwnedWindowDiff((POPUP,), ())) is True


def test_window_disappeared_matches_only_baseline_windows_that_are_gone():
    judge = window_disappeared(class_is("PopupWndClass"))
    assert judge(GOAL, OBS, OwnedWindowDiff((POPUP,), ())) is None
    assert judge(GOAL, OBS, OwnedWindowDiff((), (POPUP,))) is True


def test_first_verdict_lets_a_failure_oracle_outrank_success():
    judge = first_verdict(window_appeared(title_is("Error"), verdict=False), window_appeared(class_is("PopupWndClass")))
    assert judge(GOAL, OBS, OwnedWindowDiff((POPUP, ERROR), ())) is False
    assert judge(GOAL, OBS, OwnedWindowDiff((POPUP,), ())) is True
    assert judge(GOAL, OBS, OwnedWindowDiff((), ())) is None


def test_windows_run_coordinator_verifies_open_then_close_against_each_goals_own_baseline():
    enumerator = FakeEnumerator(
        (MAIN,), (MAIN,), (MAIN, POPUP),  # g1: baseline, pre-check, after click
        (MAIN, POPUP), (MAIN, POPUP), (MAIN,),  # g2: baseline, pre-check, after click
    )
    judge = by_goal_id(
        {
            "open": window_appeared(class_is("PopupWndClass")),
            "close": window_disappeared(class_is("PopupWndClass")),
        }
    )
    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(),
        decision_provider=ScriptedProvider("open", "open"),
        verifier=WindowsOwnedWindowVerifier(enumerator, process_id=7, judge=judge),
    )
    result = coordinator.execute(
        WindowsRunRequest(
            run_id="w2",
            target_id="screen:100:7",
            goals=(Goal("open", "open the popup"), Goal("close", "close the popup")),
            allowed_operations=("click",),
        )
    )
    assert result.status == "completed"
    assert [goal.outcome for goal in result.goals] == ["verified_success", "verified_success"]


def test_by_goal_id_gives_no_verdict_for_an_unregistered_goal():
    judge = by_goal_id({"open": lambda *_: True})
    assert judge(Goal("open", "x"), OBS, OwnedWindowDiff((), ())) is True
    assert judge(Goal("other", "x"), OBS, OwnedWindowDiff((), ())) is None


def test_a_guarded_target_is_blocked_before_any_adapter_is_opened():
    opened = []
    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda request: opened.append(request) or FakeAdapter(),
        decision_provider=ScriptedProvider("open"),
        target_guard=lambda target_id: "target is protected" if target_id == "screen:100:7" else None,
    )
    result = coordinator.execute(
        WindowsRunRequest(
            run_id="w-guard",
            target_id="screen:100:7",
            goals=(Goal("g1", "minimize this window"),),
            allowed_operations=("click",),
        )
    )
    assert opened == []
    assert result.status == "stopped"
    assert result.goals[0].termination_reason == "blocked"
    assert result.goals[0].mutation_state == "none"
