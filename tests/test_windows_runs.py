from finitact.action_adapter import Freshness, MutationResult, Observation
from finitact.contracts import Decision, ObservedCandidate
from finitact.decision_cache import InMemoryDecisionCache
from finitact.runs import CandidatePick, Goal, WindowsRunCoordinator, WindowsRunRequest


class ScriptedProvider:
    def __init__(self, *choices):
        self.choices = iter(choices)
        self.requests = []

    def decide(self, request, *, attempts, call_id):
        self.requests.append(request)
        attempts.append({"status": "confirmed", "call_id": call_id})
        choice = next(self.choices)
        return Decision(terminal_reason=choice.lower()) if choice in {"DONE", "BLOCKED"} else Decision(choice=choice)


class FakeAdapter:
    ownership = "attached"

    def __init__(self):
        self.item = ObservedCandidate("save", "click", "Save", "runtime-1")
        self.semantic = 1
        self.acts = []
        self.closed = False

    def observe(self):
        return Observation(f"obs-{self.semantic}", f"sem-{self.semantic}", (self.item,), True)

    def fresh(self, observation, candidate=None):
        return Freshness.FRESH

    def act(self, candidate, observation, text=None):
        self.acts.append((candidate.id, text))
        self.semantic += 1
        return MutationResult(candidate.id, "pattern", "confirmed")

    def close(self):
        self.closed = True


def request(**overrides):
    values = {
        "run_id": "windows-1",
        "target_id": "notepad-main",
        "goals": (Goal("g1", "Save the document"),),
        "allowed_operations": ("click",),
    }
    values.update(overrides)
    return WindowsRunRequest(**values)


def test_windows_coordinator_runs_provider_adapter_and_replays_without_second_mutation():
    adapter = FakeAdapter()
    provider = ScriptedProvider("save", "DONE")
    coordinator = WindowsRunCoordinator(adapter_factory=lambda _request: adapter, decision_provider=provider)

    result = coordinator.execute(request())
    replay = coordinator.execute(request())

    assert result.status == "completed"
    assert result.goals[0].mutation_state == "confirmed"
    assert adapter.acts == [("save", None)]
    assert adapter.closed
    assert replay.replayed
    assert provider.requests[0].candidates[0].id == "save"


def test_completed_windows_run_replays_after_coordinator_restart(tmp_path):
    ledger = tmp_path / "windows-runs.sqlite3"
    adapter = FakeAdapter()
    WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("DONE"),
        ledger_path=ledger,
    ).execute(request())

    replacement = FakeAdapter()
    replay = WindowsRunCoordinator(
        adapter_factory=lambda _request: replacement,
        decision_provider=ScriptedProvider("DONE"),
        ledger_path=ledger,
    ).execute(request())

    assert replay.replayed
    assert replacement.closed is False


def test_opt_in_decision_cache_skips_provider_but_repeats_freshness_and_mutation_for_a_new_run():
    cache = InMemoryDecisionCache()
    provider = ScriptedProvider("save")

    def verifier(_goal, observation):
        return observation.semantic_id == "sem-2"

    first_adapter = FakeAdapter()
    first = WindowsRunCoordinator(
        adapter_factory=lambda _request: first_adapter,
        decision_provider=provider,
        decision_cache=cache,
        verifier=verifier,
    )
    first.execute(request(run_id="cached-1", decision_cache_allowed=True))

    second_adapter = FakeAdapter()
    second = WindowsRunCoordinator(
        adapter_factory=lambda _request: second_adapter,
        decision_provider=provider,
        decision_cache=cache,
        verifier=verifier,
    )
    cached_result = second.execute(request(run_id="cached-2", decision_cache_allowed=True))

    assert len(provider.requests) == 1
    assert first_adapter.acts == [("save", None)]
    assert second_adapter.acts == [("save", None)]
    assert cached_result.goals[0].metrics == {
        "provider_attempts": 0,
        "decision_cache_hits": 1,
        "mutation_attempts": 1,
    }


def test_decision_cache_is_disabled_by_default():
    cache = InMemoryDecisionCache()
    provider = ScriptedProvider("DONE", "DONE")
    def coordinator():
        return WindowsRunCoordinator(
            adapter_factory=lambda _request: FakeAdapter(),
            decision_provider=provider,
            decision_cache=cache,
        )

    coordinator().execute(request(run_id="uncached-1"))
    coordinator().execute(request(run_id="uncached-2"))

    assert len(provider.requests) == 2


def test_interrupted_windows_run_is_never_automatically_restarted(tmp_path):
    ledger = tmp_path / "windows-runs.sqlite3"
    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(),
        decision_provider=ScriptedProvider("DONE"),
        ledger_path=ledger,
    )
    wanted = request()
    coordinator._save_entry(wanted.run_id, wanted.fingerprint(), "running", None)

    replacement = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(),
        decision_provider=ScriptedProvider("DONE"),
        ledger_path=ledger,
    )
    try:
        replacement.execute(wanted)
    except RuntimeError as exc:
        assert "new run_id" in str(exc)
    else:
        raise AssertionError("interrupted run was restarted")
    # BUG-0028: pollers need a terminal status, and cancel must not claim to reach a dead run.
    assert replacement.journal(wanted.run_id)["status"] == "interrupted"
    assert replacement.cancel(wanted.run_id) is False


def test_operation_allowlist_filters_provider_candidates_before_adapter_act():
    adapter = FakeAdapter()
    provider = ScriptedProvider("BLOCKED")
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=provider
    ).execute(request(allowed_operations=("fill",)))
    assert result.goals[0].termination_reason == "provider_blocked"
    assert result.goals[0].mutation_state == "none"
    assert provider.requests[0].candidates == ()
    assert adapter.acts == []


def test_operation_allowlist_exposes_only_executable_candidates():
    adapter = FakeAdapter()
    provider = ScriptedProvider("save", "DONE")
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=provider
    ).execute(request(allowed_operations=("click", "fill")))
    assert result.status == "completed"
    assert [candidate.operation for candidate in provider.requests[0].candidates] == ["click"]


class RefusingTextHelper:
    def generate(self, context, **_kwargs):
        raise AssertionError("text helper called although fill_values gave the value")


def test_fill_value_is_delivered_verbatim_and_shown_to_the_provider_without_the_text_helper():
    adapter = FakeAdapter()
    adapter.item = ObservedCandidate("field", "fill", "Editor", "runtime-1")
    provider = ScriptedProvider("field", "DONE")
    value = 'value = "Finitact 日本語"'
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=provider, text_helper=RefusingTextHelper()
    ).execute(request(allowed_operations=("fill",), fill_values={"g1": value}))

    assert result.goals[0].termination_reason == "provider_done"
    assert adapter.acts == [("field", value)]
    assert all(item.goal_inputs == {"fill_value": value} for item in provider.requests)


def test_fill_value_sets_a_slider_through_set_range():
    # BUG-0050: the caller's value was passed only to fill, so a slider could not be set.
    adapter = FakeAdapter()
    adapter.item = ObservedCandidate("volume", "set_range", "Volume", "runtime-1")
    provider = ScriptedProvider("volume", "DONE")
    WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=provider, text_helper=RefusingTextHelper()
    ).execute(request(allowed_operations=("fill", "set_range"), fill_values={"g1": "30"}))
    assert adapter.acts == [("volume", "30")]


def test_fill_values_reject_unknown_goal_blank_and_oversized_text():
    for values in ({"missing": "x"}, {"g1": " "}, {"g1": "x" * 2001}):
        try:
            request(fill_values=values)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid fill value accepted: {values!r}"[:80])


def test_fill_value_is_part_of_the_decision_cache_key_only_when_given():
    from finitact.decision_cache import decision_request_key
    from finitact.contracts import DecisionRequest

    base = DecisionRequest("g", "o", (), {}, (), 1)
    assert decision_request_key(base) == decision_request_key(DecisionRequest("g", "o", (), {}, (), 1, {}))
    assert decision_request_key(base) != decision_request_key(DecisionRequest("g", "o", (), {}, (), 1, {"fill_value": "a"}))


def test_unclassified_act_failure_is_uncertain_and_never_retried():
    class FailingAdapter(FakeAdapter):
        def act(self, candidate, observation, text=None):
            self.acts.append((candidate.id, text))
            raise RuntimeError("delivery failed")

    adapter = FailingAdapter()
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=ScriptedProvider("save")
    ).execute(request())
    assert result.goals[0].mutation_state == "uncertain"
    assert adapter.acts == [("save", None)]


def test_stale_choice_is_discarded_and_redecided():
    class OnceStaleAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.checks = 0

        def fresh(self, observation, candidate=None):
            self.checks += 1
            return Freshness.STALE if self.checks == 1 else Freshness.FRESH

    adapter = OnceStaleAdapter()
    provider = ScriptedProvider("save", "save", "DONE")
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=provider
    ).execute(request())
    assert result.status == "completed"
    assert adapter.acts == [("save", None)]


def test_verified_initial_state_completes_without_provider_or_mutation():
    adapter = FakeAdapter()
    provider = ScriptedProvider()
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=provider,
        verifier=lambda _goal, _observation: True,
    ).execute(request())
    assert result.status == "completed"
    assert result.goals[0].termination_reason == "outcome_verified"
    assert result.goals[0].mutation_state == "none"
    assert result.goals[0].outcome == "verified_success"
    assert provider.requests == []
    assert adapter.acts == []


def test_incomplete_initial_observation_never_reaches_verifier():
    class IncompleteAdapter(FakeAdapter):
        def observe(self):
            return Observation("partial", "partial", (), False, read_errors=("walk",))

    called = []
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: IncompleteAdapter(),
        decision_provider=ScriptedProvider(),
        verifier=lambda *_: called.append(True) or True,
    ).execute(request())
    assert result.status == "stopped"
    assert result.goals[0].termination_reason == "error"
    assert result.goals[0].detail == "incomplete observation"
    assert called == []


def test_verified_postcondition_stops_before_false_continuation():
    adapter = FakeAdapter()
    provider = ScriptedProvider("save", "save")
    checks = iter([False, True])
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=provider,
        verifier=lambda _goal, _observation: next(checks),
    ).execute(request())
    assert result.status == "completed"
    assert result.goals[0].termination_reason == "outcome_verified"
    assert result.goals[0].mutation_state == "confirmed"
    assert result.goals[0].outcome == "verified_success"
    assert adapter.acts == [("save", None)]
    assert len(provider.requests) == 1


def test_incomplete_post_mutation_observation_never_reaches_verifier():
    class BecomesIncompleteAdapter(FakeAdapter):
        def observe(self):
            complete = self.semantic == 1
            return Observation(
                f"obs-{self.semantic}",
                f"sem-{self.semantic}",
                (self.item,) if complete else (),
                complete,
                read_errors=() if complete else ("walk",),
            )

    adapter = BecomesIncompleteAdapter()
    checks = []
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("save"),
        verifier=lambda *_: checks.append(True) or False,
    ).execute(request())
    assert result.status == "stopped"
    assert result.goals[0].detail == "post-mutation observation is incomplete"
    assert checks == [True]


def test_verified_goal_continues_to_the_next_ordered_goal():
    adapter = FakeAdapter()
    checks = iter([True, True])
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider(),
        verifier=lambda _goal, _observation: next(checks),
    ).execute(request(goals=(Goal("g1", "First"), Goal("g2", "Second"))))
    assert result.status == "completed"
    assert [goal.termination_reason for goal in result.goals] == ["outcome_verified", "outcome_verified"]
    assert result.remaining_goal_ids == ()


def test_verifier_exception_after_mutation_preserves_confirmed_history_as_error():
    adapter = FakeAdapter()
    checks = iter([False, RuntimeError("oracle failed")])

    def verifier(_goal, _observation):
        value = next(checks)
        if isinstance(value, Exception):
            raise value
        return value

    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("save"),
        verifier=verifier,
    ).execute(request())
    assert result.status == "stopped"
    assert result.goals[0].termination_reason == "error"
    assert result.goals[0].mutation_state == "confirmed"
    assert result.goals[0].outcome == "unverified"
    assert result.goals[0].detail == "outcome verification failed: RuntimeError"


def test_synthetic_opt_in_requires_runtime_environment_reference():
    try:
        request(synthetic_input_allowed=True)
    except ValueError as exc:
        assert "exclusive environment" in str(exc)
    else:
        raise AssertionError("unsafe request was accepted")


def test_goal_metrics_sum_integer_provider_usage_across_decisions():
    class UsageProvider(ScriptedProvider):
        def decide(self, request, *, attempts, call_id):
            decision = super().decide(request, attempts=attempts, call_id=call_id)
            usage = {"prompt_tokens": 100, "completion_tokens": 5, "model": "jev-test", "cached": True}
            return Decision(decision.choice, decision.terminal_reason, measurements={"usage": usage})

    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(), decision_provider=UsageProvider("save", "DONE")
    )

    metrics = coordinator.execute(request()).goals[0].metrics

    assert metrics == {
        "provider_attempts": 2,
        "decision_cache_hits": 0,
        "mutation_attempts": 1,
        "provider_usage_completion_tokens": 10,
        "provider_usage_prompt_tokens": 200,
    }


def test_goal_stage_ms_covers_loop_stages_heats_and_adapter_internals():
    class HeatProvider(ScriptedProvider):
        def decide(self, request, *, attempts, call_id):
            decision = super().decide(request, attempts=attempts, call_id=call_id)
            heats = [{"latency_ms": 30}, {"latency_ms": 50}]
            return Decision(decision.choice, decision.terminal_reason, provider_metadata={"heats": heats})

    class TimedAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.stage_ms = {"observe_extract": 100}

        def observe(self):
            self.stage_ms["observe_extract"] += 7
            return super().observe()

    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: TimedAdapter(), decision_provider=HeatProvider("save", "DONE")
    )

    stage_ms = coordinator.execute(request()).goals[0].stage_ms

    assert {"observe", "observe_initial", "observe_post_mutation", "decide", "fresh", "act"} <= set(stage_ms)
    assert stage_ms["decide_requests"] == 6
    assert stage_ms["decide_heats"] == 160
    assert stage_ms["decide_heats_max"] == 100
    # Diffed against the adapter's counter at goal start: initial and post-mutation observe only.
    assert stage_ms["adapter_observe_extract"] == 14


class JudgedProvider:
    """Returns decisions with the TypeSafe metadata shape (confidence + probabilities)."""

    def __init__(self, *decisions):
        self.decisions = iter(decisions)

    def decide(self, request, *, attempts, call_id):
        attempts.append({"status": "confirmed", "call_id": call_id})
        choice, confidence, probabilities = next(self.decisions)
        metadata = {"confidence": confidence, "probabilities": probabilities}
        if choice in {"DONE", "BLOCKED"}:
            return Decision(terminal_reason=choice.lower(), provider_metadata=metadata)
        return Decision(choice=choice, provider_metadata=metadata)


class ManyCandidatesAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.items = tuple(
            ObservedCandidate(f"c{index}", "click", f"Label {index}", f"runtime-{index}") for index in range(7)
        ) + (ObservedCandidate("dup", "click", "Label 0", "runtime-dup"),)

    def observe(self):
        return Observation(f"obs-{self.semantic}", f"sem-{self.semantic}", self.items, True)


def test_low_confidence_choice_stops_without_acting_and_returns_likely_labels(tmp_path):
    adapter = ManyCandidatesAdapter()
    probabilities = {"c6": 0.30, "BLOCKED": 0.25, "c0": 0.15, "dup": 0.1, "c2": 0.08, "c4": 0.06, "c5": 0.04,
                     "c1": 0.02}
    ledger = tmp_path / "windows-runs.sqlite3"
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=JudgedProvider(("c6", 0.30, probabilities)),
        ledger_path=ledger,
    ).execute(request())

    goal = result.goals[0]
    assert goal.termination_reason == "provider_uncertain"
    assert goal.mutation_state == "none" and adapter.acts == []
    assert [item["label"] for item in goal.screen_candidates] == ["Label 6", "Label 0", "Label 2", "Label 4", "Label 5"]
    assert goal.screen_candidates[0] == {"ref": "c6", "label": "Label 6"}
    replay = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(), decision_provider=JudgedProvider(), ledger_path=ledger
    ).execute(request())
    assert replay.goals[0].screen_candidates == goal.screen_candidates


def test_likely_labels_skip_shape_only_edge_candidates():
    adapter = ManyCandidatesAdapter()
    adapter.items = (
        ObservedCandidate("e1", "click", "boxed_region_296x907", "runtime-e1", {"source": "edge_contour"}),
    ) + adapter.items
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=JudgedProvider(("e1", 0.30, {"e1": 0.30, "c3": 0.2, "BLOCKED": 0.1})),
    ).execute(request())

    assert result.goals[0].termination_reason == "provider_uncertain"
    assert result.goals[0].screen_candidates == ({"ref": "c3", "label": "Label 3"},)


def test_low_confidence_blocked_is_uncertain_but_confident_terminals_and_low_done_are_kept():
    def run(choice, confidence):
        return WindowsRunCoordinator(
            adapter_factory=lambda _request: FakeAdapter(),
            decision_provider=JudgedProvider((choice, confidence, {choice: confidence, "save": 1 - confidence})),
        ).execute(request()).goals[0]

    low_blocked = run("BLOCKED", 0.2)
    assert low_blocked.termination_reason == "provider_uncertain"
    assert low_blocked.screen_candidates == ({"ref": "save", "label": "Save"},)
    assert run("BLOCKED", 0.49).termination_reason == "provider_uncertain"
    assert run("BLOCKED", 0.9).termination_reason == "provider_blocked"
    assert run("BLOCKED", 0.9).screen_candidates == ()
    assert run("DONE", 0.2).termination_reason == "provider_done"


def test_confident_choice_acts_as_before():
    adapter = FakeAdapter()
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=JudgedProvider(("save", 0.4, {"save": 0.4}), ("DONE", 0.9, {"DONE": 0.9})),
    ).execute(request())

    assert result.status == "completed"
    assert adapter.acts == [("save", None)]


class DragAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.start = ObservedCandidate(
            "start", "drag", "Choose drag start at 'Slider'", "slider",
            {"drag_phase": "start", "source": "ocr"},
        )
        self.end = ObservedCandidate(
            "end", "drag", "Drag from 'Slider' to shape", "edge",
            {"drag_phase": "end", "source": "edge_contour"},
        )
        self.transitions = 0

    def observe(self):
        return Observation("drag-start-obs", "drag-start-sem", (self.start,), True)

    def drag_end_observation(self, candidate, observation):
        assert candidate is self.start
        self.transitions += 1
        return Observation("drag-end-obs", "drag-end-sem", (self.end,), True)


def test_drag_uses_two_provider_decisions_but_one_action_and_mutation():
    adapter = DragAdapter()
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("start", "end", "DONE"),
    ).execute(request(allowed_operations=("drag",), action_budget=1, provider_attempt_budget=3))

    goal = result.goals[0]
    assert goal.termination_reason == "provider_done"
    assert goal.metrics["provider_attempts"] == 3
    assert goal.metrics["mutation_attempts"] == 1
    assert adapter.transitions == 1 and adapter.acts == [("end", None)]


class DroppingAdapter(DragAdapter):
    def __init__(self):
        super().__init__()
        self.start = ObservedCandidate(
            "start", "drag", "Choose drag start at 'Slider'", "slider",
            {"drag_phase": "start", "source": "ocr"}, {"original_label": "Slider"},
        )

    def observe(self):
        if not self.acts:
            return super().observe()
        dropped = ObservedCandidate("zone", "click", "OT SOURCE CAR", "zone", {"source": "ocr"})
        return Observation("dropped-obs", "dropped-sem", (dropped, self.end), True)


def test_unverified_windows_goal_that_acted_reports_labels_gone_and_new():
    adapter = DroppingAdapter()
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("start", "end", "DONE"),
    ).execute(request(allowed_operations=("drag", "click")))

    goal = result.goals[0]
    assert goal.outcome == "unverified" and goal.mutation_state == "confirmed"
    # The drag start is named by its region, and the shape-only end is not a label.
    assert goal.screen_changes == {"gone": ["Slider"], "new": ["OT SOURCE CAR"]}


def test_goal_without_mutation_or_on_a_browser_carries_no_screen_changes():
    idle = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(), decision_provider=ScriptedProvider("DONE")
    ).execute(request())
    browser = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(), decision_provider=ScriptedProvider("save", "DONE")
    ).execute(request(target_id="browser:https://example.test/"))
    for result in (idle, browser):
        assert result.goals[0].screen_changes is None and "screen_changes" not in result.record()["goals"][0]


def test_drag_provider_budget_can_stop_between_selections_without_mutation():
    adapter = DragAdapter()
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("start"),
    ).execute(request(allowed_operations=("drag",), action_budget=1, provider_attempt_budget=1))

    assert result.goals[0].termination_reason == "budget"
    assert adapter.transitions == 1 and adapter.acts == []


def test_click_label_constraint_hides_and_blocks_other_click_targets():
    adapter = FakeAdapter()
    adapter.items = (
        ObservedCandidate("wrong", "click", "Item 15 entry", "wrong"),
        ObservedCandidate("down", "scroll", "Scroll down", "scroll:down"),
    )
    adapter.observe = lambda: Observation("obs-1", "sem-1", adapter.items, True)
    provider = ScriptedProvider("wrong")
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=provider
    ).execute(request(allowed_operations=("click", "scroll"), click_label_constraints={"g1": "TARGET ROW"}))

    assert [candidate.id for candidate in provider.requests[0].candidates] == ["down"]
    assert result.goals[0].termination_reason == "blocked"
    assert result.goals[0].detail == "provider selected a candidate denied by policy"
    assert adapter.acts == []


def test_click_label_constraint_allows_one_exact_match_but_not_duplicates():
    def run(items):
        adapter = FakeAdapter()
        adapter.items = items
        adapter.observe = lambda: Observation("obs-1", "sem-1", adapter.items, True)
        provider = ScriptedProvider("target", "DONE")
        result = WindowsRunCoordinator(
            adapter_factory=lambda _request: adapter, decision_provider=provider
        ).execute(request(click_label_constraints={"g1": "TARGET ROW"}))
        return adapter, provider, result

    target = ObservedCandidate("target", "click", "TARGET ROW", "target")
    adapter, provider, result = run((target, ObservedCandidate("wrong", "click", "Item 31 entry", "wrong")))
    assert [candidate.id for candidate in provider.requests[0].candidates] == ["target"]
    assert result.goals[0].termination_reason == "provider_done"
    assert adapter.acts == [("target", None)]

    adapter, provider, result = run((target, ObservedCandidate("target-2", "click", "TARGET ROW", "other")))
    assert provider.requests[0].candidates == ()
    assert result.goals[0].termination_reason == "blocked"
    assert adapter.acts == []


def test_click_label_constraint_rejects_unknown_goal_and_blank_label():
    for constraints in ({"missing": "TARGET ROW"}, {"g1": " "}):
        try:
            request(click_label_constraints=constraints)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid click label constraint accepted: {constraints}")


class RetainingAdapter(ManyCandidatesAdapter):
    """Shares one screen across adapters, as consecutive runs on one window do."""

    def __init__(self, screen):
        super().__init__()
        self.screen = screen
        self.adopted = []
        self.observes = 0

    def observe(self):
        self.observes += 1
        return Observation(f"obs-{self.screen['version']}", f"sem-{self.screen['version']}", self.items, True)

    def fresh(self, observation, candidate=None):
        current = f"obs-{self.screen['version']}"
        return Freshness.FRESH if observation.observation_id == current else Freshness.STALE

    def act(self, candidate, observation, text=None):
        self.acts.append((candidate.id, text))
        self.screen["version"] += 1
        return MutationResult(candidate.id, "synthetic_pointer", "confirmed")

    def retain(self, observation):
        return ("frames", observation.observation_id)

    def adopt(self, observation, frames):
        assert frames == ("frames", observation.observation_id)
        self.adopted.append(observation.observation_id)


UNCERTAIN = ("c6", 0.30, {"c6": 0.30, "c2": 0.2, "BLOCKED": 0.1})


def _pick_setup():
    screen = {"version": 1}
    adapters = []

    def factory(_request):
        adapters.append(RetainingAdapter(screen))
        return adapters[-1]

    return screen, adapters, factory


def test_ref_acts_on_an_uncertain_runs_candidate_without_asking_the_provider_first():
    _screen, adapters, factory = _pick_setup()
    coordinator = WindowsRunCoordinator(
        adapter_factory=factory, decision_provider=JudgedProvider(UNCERTAIN, ("DONE", 0.9, {"DONE": 0.9}))
    )
    first = coordinator.execute(request(run_id="u-1")).goals[0]
    assert first.termination_reason == "provider_uncertain"

    # ADR-0043: the uncertain run's last observation is the window's latest, so the goal may be restated.
    restated = (Goal("g1", "Click Label 2"),)
    result = coordinator.execute(request(run_id="u-2", goals=restated, pick=CandidatePick("c2"))).goals[0]

    assert result.termination_reason == "provider_done"
    assert adapters[1].adopted == ["obs-1"]
    assert adapters[1].acts == [("c2", None)]
    # Only the DONE check reached the provider, and the stale pre-pick observe was skipped.
    assert result.metrics["provider_attempts"] == 1 and result.metrics["direct_picks"] == 1
    assert adapters[1].observes == 1


def test_an_uncertain_runs_click_candidate_is_taken_as_offered_even_for_a_fill_goal():
    _screen, adapters, factory = _pick_setup()
    coordinator = WindowsRunCoordinator(
        adapter_factory=factory, decision_provider=JudgedProvider(UNCERTAIN, ("DONE", 0.9, {"DONE": 0.9}))
    )
    fill = {"allowed_operations": ("click", "fill"), "fill_values": {"g1": "hello"}}
    coordinator.execute(request(run_id="u-1", **fill))

    result = coordinator.execute(request(run_id="u-2", pick=CandidatePick("c2"), **fill)).goals[0]

    assert result.termination_reason == "provider_done" and adapters[1].acts == [("c2", None)]


def test_ref_does_not_act_when_the_window_changed_and_is_consumed_once():
    screen, adapters, factory = _pick_setup()
    coordinator = WindowsRunCoordinator(adapter_factory=factory, decision_provider=JudgedProvider(UNCERTAIN))
    coordinator.execute(request(run_id="u-1"))
    screen["version"] += 1

    stale = coordinator.execute(request(run_id="u-2", pick=CandidatePick("c6"))).goals[0]
    again = coordinator.execute(request(run_id="u-3", pick=CandidatePick("c6"))).goals[0]

    assert (stale.termination_reason, stale.mutation_state) == ("blocked", "none")
    assert stale.detail.startswith("pick:") and adapters[1].acts == []
    assert again.termination_reason == "blocked" and "observe the window again" in again.detail


def test_ref_rejects_unoffered_refs_other_targets_and_adapters_that_retain_nothing():
    _screen, _adapters, factory = _pick_setup()
    coordinator = WindowsRunCoordinator(adapter_factory=factory, decision_provider=JudgedProvider(UNCERTAIN))
    coordinator.execute(request(run_id="u-1"))
    # "c5" existed on screen but was not among the offered candidates.
    unoffered = coordinator.execute(request(run_id="p-1", pick=CandidatePick("c5"))).goals[0]
    coordinator.execute(request(run_id="u-2"))
    other = coordinator.execute(request(run_id="p-2", target_id="other", pick=CandidatePick("c6"))).goals[0]
    plain = WindowsRunCoordinator(adapter_factory=lambda _request: ManyCandidatesAdapter(),
                                  decision_provider=JudgedProvider(UNCERTAIN))
    plain.execute(request(run_id="u-1"))
    unretained = plain.execute(request(run_id="p-3", pick=CandidatePick("c6"))).goals[0]

    assert "not offered" in unoffered.detail
    assert "observe the window again" in other.detail and "observe the window again" in unretained.detail
    assert all(goal.termination_reason == "blocked" for goal in (unoffered, other, unretained))


def _observed(coordinator, adapter, target_id="notepad-main"):
    observation = adapter.observe()
    refs = {"1": {"click": "c2"}, "2": {"click": "c6", "fill": "f6"}}
    coordinator.keep_observation(target_id, observation, adapter.retain(observation), refs)


def test_a_screen_observe_ref_is_refused_on_the_uia_path_by_name_and_ended_by_that_run():
    # BUG-0049: the refusal said "no observation", and the outer agent repeated the same call six times.
    screen, _adapters, factory = _pick_setup()
    coordinator = WindowsRunCoordinator(adapter_factory=factory, decision_provider=JudgedProvider(UNCERTAIN))
    _observed(coordinator, RetainingAdapter(screen), target_id="screen:10:20")

    uia = coordinator.execute(request(run_id="x-1", target_id="uia:10:20", pick=CandidatePick("1"))).goals[0]
    after = coordinator.execute(request(run_id="x-2", target_id="screen:10:20", pick=CandidatePick("1"))).goals[0]

    assert uia.termination_reason == "blocked" and "synthetic_input_allowed" in uia.detail
    assert "observe the window again" in after.detail


def test_observe_ref_acts_without_the_chooser_and_resolves_fill_by_the_goal():
    screen, adapters, factory = _pick_setup()
    provider = JudgedProvider(("DONE", 0.9, {"DONE": 0.9}), ("DONE", 0.9, {"DONE": 0.9}))
    coordinator = WindowsRunCoordinator(adapter_factory=factory, decision_provider=provider)
    probe = RetainingAdapter(screen)
    probe.items += (ObservedCandidate("f6", "fill", "Label 6", "runtime-6"),)

    _observed(coordinator, probe)
    clicked = coordinator.execute(request(run_id="o-1", pick=CandidatePick("1")))
    _observed(coordinator, probe)
    filled = coordinator.execute(
        request(
            run_id="o-2",
            allowed_operations=("click", "fill"),
            fill_values={"g1": "hello"},
            pick=CandidatePick("2"),
        )
    )

    assert [goal.termination_reason for goal in (clicked.goals[0], filled.goals[0])] == ["provider_done"] * 2
    assert adapters[0].acts == [("c2", None)] and adapters[1].acts == [("f6", "hello")]
    assert clicked.goals[0].metrics["direct_picks"] == 1 and adapters[0].observes == 1


def test_observe_ref_is_rejected_when_expired_absent_or_superseded():
    screen, adapters, factory = _pick_setup()
    now = [0.0]
    coordinator = WindowsRunCoordinator(
        adapter_factory=factory, decision_provider=JudgedProvider(UNCERTAIN), clock=lambda: now[0]
    )
    probe = RetainingAdapter(screen)

    _observed(coordinator, probe)
    now[0] = 61.0
    late = coordinator.execute(request(run_id="o-1", pick=CandidatePick("1"))).goals[0]
    _observed(coordinator, probe)
    # A run on the window without a ref also ends its refs.
    coordinator.execute(request(run_id="o-3"))
    after_run = coordinator.execute(request(run_id="o-4", pick=CandidatePick("1"))).goals[0]
    _observed(coordinator, probe, target_id="other")
    wrong_target = coordinator.execute(request(run_id="o-5", pick=CandidatePick("1"))).goals[0]
    _observed(coordinator, probe)
    no_ref = coordinator.execute(request(run_id="o-6", pick=CandidatePick("9"))).goals[0]
    _observed(coordinator, probe)
    fill_denied = coordinator.execute(request(run_id="o-7", fill_values={"g1": "x"}, pick=CandidatePick("2"))).goals[0]
    _observed(coordinator, probe)
    # A click-only item under a fill goal is clicked as offered, so it still needs click allowed.
    click_denied = coordinator.execute(
        request(run_id="o-8", allowed_operations=("fill",), fill_values={"g1": "x"}, pick=CandidatePick("1"))
    ).goals[0]

    assert "expired" in late.detail
    # The uncertain run o-3 kept its own candidates; "1" is not among them.
    assert "not offered" in after_run.detail and "observe the window again" in wrong_target.detail
    assert "not offered" in no_ref.detail
    assert fill_denied.detail == "pick: fill is not an allowed operation"
    assert click_denied.detail == "pick: click is not an allowed operation"
    assert all(adapter.acts == [] for adapter in adapters)


def test_ref_is_part_of_the_run_fingerprint():
    assert request().fingerprint() != request(pick=CandidatePick("c6")).fingerprint()


def test_likely_labels_keep_a_fill_beside_the_click_of_the_same_region():
    adapter = ManyCandidatesAdapter()
    adapter.items = (ObservedCandidate("f0", "fill", "Label 0", "runtime-0"),) + adapter.items
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=JudgedProvider(("f0", 0.30, {"f0": 0.30, "c0": 0.2, "BLOCKED": 0.1})),
    ).execute(request(allowed_operations=("click", "fill")))

    assert result.goals[0].screen_candidates == (
        {"ref": "f0", "label": "Label 0", "kind": "fill"},
        {"ref": "c0", "label": "Label 0"},
    )


class ListAdapter(FakeAdapter):
    """A list of rows seen through a window; the wheel moves it and stops at either end."""

    def __init__(self, rows, *, visible=3, top=0, blocks=1):
        super().__init__()
        self.rows, self.visible, self.top, self.blocks = rows, visible, top, blocks

    def observe(self):
        shown = tuple(
            ObservedCandidate(f"row-{self.top + i}", "click", label, f"r{self.top + i}")
            for i, label in enumerate(self.rows[self.top:self.top + self.visible])
        )
        scrolls = tuple(
            ObservedCandidate(f"{direction}-{block}", "scroll", f"Scroll {direction}", f"scroll:{direction}",
                              {"direction": direction, "rect": [0, block * 100, 50, 90]})
            for block in range(self.blocks)
            for direction in ("up", "down")
        )
        return Observation(f"obs-{self.top}", f"sem-{self.top}", shown + scrolls, True)

    def act(self, candidate, observation, text=None):
        self.acts.append(candidate.id)
        if candidate.operation == "scroll":
            step = 1 if candidate.attributes["direction"] == "down" else -1
            self.top = min(max(self.top + step, 0), len(self.rows) - self.visible)
        return MutationResult(candidate.id, "synthetic_pointer", "confirmed")


def find_request(**overrides):
    values = {
        "allowed_operations": ("click", "scroll"),
        "click_label_constraints": {"g1": "TARGET"},
        "selection_policies": {"g1": "find_and_click"},
    }
    return request(**{**values, **overrides})


def test_find_and_click_scrolls_down_and_clicks_without_asking_the_provider_until_done():
    adapter = ListAdapter(["a", "b", "c", "d", "TARGET", "f"])
    provider = ScriptedProvider("DONE")
    goal = WindowsRunCoordinator(adapter_factory=lambda _r: adapter, decision_provider=provider).execute(
        find_request()
    ).goals[0]

    assert adapter.acts == ["down-0", "down-0", "row-4"]
    assert goal.termination_reason == "provider_done"
    assert goal.metrics["provider_attempts"] == 1 and goal.metrics["policy_actions"] == 3


def test_find_and_click_turns_up_once_at_the_bottom_and_does_not_bounce():
    adapter = ListAdapter(["TARGET", "b", "c", "d", "e", "f"], top=2)
    WindowsRunCoordinator(adapter_factory=lambda _r: adapter, decision_provider=ScriptedProvider("DONE")).execute(
        find_request()
    )

    assert adapter.acts == ["down-0", "down-0", "up-0", "up-0", "up-0", "row-0"]


def test_find_and_click_stops_unfound_after_both_ends_without_the_provider():
    adapter = ListAdapter(["a", "b", "c", "d"], top=0)
    provider = ScriptedProvider()
    goal = WindowsRunCoordinator(adapter_factory=lambda _r: adapter, decision_provider=provider).execute(
        find_request()
    ).goals[0]

    assert adapter.acts == ["down-0", "down-0", "up-0", "up-0"]
    assert goal.termination_reason == "blocked" and "not found" in goal.detail
    assert provider.requests == []


def test_find_and_click_clicks_once_and_blocks_when_the_provider_wants_more():
    adapter = ListAdapter(["TARGET", "b", "c"])
    goal = WindowsRunCoordinator(
        adapter_factory=lambda _r: adapter, decision_provider=ScriptedProvider("row-0")
    ).execute(find_request()).goals[0]

    assert adapter.acts == ["row-0"]
    assert goal.termination_reason == "blocked" and "did not confirm done" in goal.detail


def test_find_and_click_hands_several_lists_back_to_the_provider():
    adapter = ListAdapter(["a", "b", "c", "TARGET"], blocks=2)
    provider = ScriptedProvider("down-1", "row-3", "DONE")
    WindowsRunCoordinator(adapter_factory=lambda _r: adapter, decision_provider=provider).execute(find_request())

    assert adapter.acts == ["down-1", "row-3"]
    assert len(provider.requests) == 2


def test_find_and_click_needs_a_click_label_constraint_and_a_known_policy():
    for overrides in ({"click_label_constraints": {}}, {"selection_policies": {"g1": "find"}}):
        try:
            find_request(**overrides)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid selection policy accepted: {overrides}")


class EvidenceAdapter(FakeAdapter):
    def screen_evidence(self, observation, candidate):
        return ("screen", observation.observation_id, candidate.id)


def test_action_verifier_judges_each_confirmed_action_from_its_before_and_after_screens():
    adapter = EvidenceAdapter()
    seen = []

    def action_verifier(outcome):
        seen.append(outcome)
        return True

    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("save"),
        action_verifier=action_verifier,
    ).execute(request())
    assert result.goals[0].termination_reason == "outcome_verified"
    assert result.goals[0].outcome == "verified_success"
    assert adapter.acts == [("save", None)]
    outcome = seen[0]
    assert (outcome.goal, outcome.candidate.id) == ("Save the document", "save")
    assert outcome.before_screen == ("screen", "obs-1", "save")
    assert outcome.after_screen == ("screen", "obs-2", "save")


def test_unverified_action_verdict_continues_and_a_failure_oracle_outranks_it():
    adapter = EvidenceAdapter()
    calls = []
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("save", "save", "DONE"),
        verifier=lambda _goal, observation: False if observation.observation_id == "obs-3" else None,
        action_verifier=lambda outcome: calls.append(outcome.after.observation_id) and None,
    ).execute(request())
    assert adapter.acts == [("save", None), ("save", None)]
    assert calls == ["obs-2"]
    assert result.goals[0].outcome == "verified_failure"


def test_adapter_without_screen_evidence_passes_none_screens():
    seen = []
    WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(),
        decision_provider=ScriptedProvider("save", "DONE"),
        action_verifier=lambda outcome: seen.append(outcome) and None,
    ).execute(request())
    assert (seen[0].before_screen, seen[0].after_screen) == (None, None)


class StillScreenAdapter(FakeAdapter):
    """Enter on an emptied message box: delivered every time, the screen never changes (E2E-I8)."""

    def __init__(self, *, change_after=None):
        super().__init__()
        self.item = ObservedCandidate("enter", "key", "Press Enter", "key:Enter")
        self.other = ObservedCandidate("tab", "key", "Press Tab", "key:Tab")
        self.change_after = change_after

    def observe(self):
        return Observation(f"obs-{self.semantic}", f"sem-{self.semantic}", (self.item, self.other), True)

    def act(self, candidate, observation, text=None):
        self.acts.append((candidate.id, text))
        if self.change_after is not None and len(self.acts) == self.change_after:
            self.semantic += 1
        return MutationResult(candidate.id, "synthetic_key", "confirmed")

    def unchanged(self, before, after):
        return before.observation_id == after.observation_id


def test_action_that_left_the_screen_unchanged_is_not_offered_again_on_that_screen():
    adapter = StillScreenAdapter()
    provider = ScriptedProvider("enter", "tab")
    coordinator = WindowsRunCoordinator(adapter_factory=lambda _request: adapter, decision_provider=provider)

    result = coordinator.execute(request(allowed_operations=("key",)))

    assert adapter.acts == [("enter", None), ("tab", None)]
    assert [item.id for item in provider.requests[1].candidates] == ["tab"]
    assert result.goals[0].termination_reason == "blocked"
    assert result.goals[0].detail == "no offered action changed the screen"


def test_a_later_screen_change_lifts_the_no_effect_exclusion():
    adapter = StillScreenAdapter(change_after=2)
    provider = ScriptedProvider("enter", "tab", "enter", "DONE")
    coordinator = WindowsRunCoordinator(adapter_factory=lambda _request: adapter, decision_provider=provider)

    coordinator.execute(request(allowed_operations=("key",)))

    assert [item.id for item in provider.requests[2].candidates] == ["enter", "tab"]
    assert adapter.acts == [("enter", None), ("tab", None), ("enter", None)]


def test_window_lists_around_the_action_and_the_goal_text_reach_the_action_verifier():
    seen = []
    probes = iter([({"hwnd": 1},), ({"hwnd": 1}, {"hwnd": 2})])

    def verifier(outcome):
        seen.append((outcome.windows_before, outcome.windows_after, outcome.goal_text))
        return True

    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: FakeAdapter(),
        decision_provider=ScriptedProvider("save"),
        action_verifier=verifier,
        window_probe=lambda: next(probes),
    )
    result = coordinator.execute(request(fill_values={"g1": "hello"}))
    assert result.goals[0].termination_reason == "outcome_verified"
    assert seen == [(({"hwnd": 1},), ({"hwnd": 1}, {"hwnd": 2}), "hello")]


def test_a_key_from_the_search_pane_is_judged_once_the_launched_window_takes_the_foreground():
    search = {"hwnd": 1, "process": "SearchHost.exe", "foreground": True}
    settings = {"hwnd": 2, "process": "ApplicationFrameHost.exe", "title": "設定", "foreground": True}
    probes = iter([(search,), (search,), (search | {"foreground": False},), (search | {"foreground": False}, settings)]
                  + [(settings,)] * 9)
    seen = []

    def verifier(outcome):
        seen.append(outcome.windows_after)
        return True

    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: StillScreenAdapter(change_after=1),
        decision_provider=ScriptedProvider("enter"),
        action_verifier=verifier,
        window_probe=lambda: next(probes),
    )
    coordinator._sleep = lambda _seconds: None
    assert coordinator.execute(request(allowed_operations=("key",))).goals[0].termination_reason == "outcome_verified"
    assert seen[0] == (settings,)


class LateDrawing(StillScreenAdapter):
    """Each pending late draw changes the screen once more after the capture that preceded it."""

    late_draws = 0
    observations = 0

    def observe(self):
        self.observations += 1
        observation = super().observe()
        if self.late_draws:
            self.late_draws -= 1
            self.semantic += 1
        return observation


def test_a_screen_that_stopped_drawing_is_not_rejudged():
    """The E2E-04 search run spent 2.3-3.2 s per click or fill re-asking the same verdict on an unchanged screen."""

    class Settled(LateDrawing):
        def act(self, candidate, observation, text=None):
            self.acts.append((candidate.id, text))
            self.semantic += 1
            return MutationResult(candidate.id, "synthetic_key", "confirmed")

    adapter = Settled()
    verdicts = []
    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("enter", "DONE"),
        action_verifier=lambda outcome: verdicts.append(outcome.after.observation_id),
    )
    coordinator._sleep = lambda _seconds: None
    coordinator.execute(request(allowed_operations=("key",)))
    assert len(verdicts) == 1
    # before the action, after it, and one settle capture that matched the judged one
    assert adapter.observations == 3


def test_a_committed_key_is_rejudged_on_a_later_observation_when_the_first_is_too_early():
    """A sent message joins the list after the ~0.15 s UIA observation (ADR-0036); the verdict waits a bounded time."""

    class Changing(LateDrawing):
        def act(self, candidate, observation, text=None):
            self.acts.append((candidate.id, text))
            self.semantic += 1
            self.late_draws = 1
            return MutationResult(candidate.id, "synthetic_key", "confirmed")

    adapter = Changing()
    verdicts = iter([None, True])
    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("enter"),
        action_verifier=lambda _outcome: next(verdicts),
    )
    coordinator._sleep = lambda _seconds: None
    result = coordinator.execute(request(allowed_operations=("key",)))
    assert result.goals[0].termination_reason == "outcome_verified"
    assert adapter.acts == [("enter", None)]


def test_a_fill_is_rejudged_on_a_later_observation_when_the_value_is_not_drawn_yet():
    """E2E-I47: a cold VSCode Quick Open showed its placeholder in the first capture after typing."""

    class Typing(LateDrawing):
        def __init__(self):
            super().__init__()
            self.item = ObservedCandidate("search", "fill", "Search", "search")

        def act(self, candidate, observation, text=None):
            self.acts.append((candidate.id, text))
            self.semantic += 1
            self.late_draws = 1
            return MutationResult(candidate.id, "synthetic_text", "confirmed")

    adapter = Typing()
    verdicts = iter([None, True])
    coordinator = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter,
        decision_provider=ScriptedProvider("search"),
        action_verifier=lambda _outcome: next(verdicts),
    )
    coordinator._sleep = lambda _seconds: None
    result = coordinator.execute(request(allowed_operations=("fill",), fill_values={"g1": "hello"}))
    assert result.goals[0].termination_reason == "outcome_verified"
    assert adapter.acts == [("search", "hello")]


def test_an_action_that_returns_to_an_earlier_state_is_not_offered_again_there():
    """E2E-I16: 199 clicks opened and closed a dropdown; every click changed the screen."""

    class Toggle(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.item = ObservedCandidate("region", "click", "Choose a Region", "r")
            self.other = ObservedCandidate("tokyo", "click", "Tokyo", "t")
            self.open = False

        def observe(self):
            items = (self.item, self.other) if self.open else (self.item,)
            state = "open" if self.open else "closed"
            return Observation(f"obs-{state}-{len(self.acts)}", f"sem-{state}", items, True)

        def act(self, candidate, observation, text=None):
            self.acts.append((candidate.id, text))
            if candidate.id == "region":
                self.open = not self.open
            return MutationResult(candidate.id, "pattern", "confirmed")

    adapter = Toggle()
    provider = ScriptedProvider("region", "region", "region", "tokyo", "DONE")
    WindowsRunCoordinator(adapter_factory=lambda _request: adapter, decision_provider=provider).execute(request())
    assert [item.id for item in provider.requests[3].candidates] == ["tokyo"]
    assert adapter.acts == [("region", None), ("region", None), ("region", None), ("tokyo", None)]


def test_a_decision_that_keeps_going_stale_stops_after_three_times():
    """E2E-02: a repainting page made the same choice stale 120 times until the provider budget ran out."""

    class Repainting(FakeAdapter):
        def fresh(self, observation, candidate=None):
            return Freshness.STALE

    adapter = Repainting()
    provider = ScriptedProvider(*["save"] * 10)
    result = WindowsRunCoordinator(adapter_factory=lambda _request: adapter, decision_provider=provider).execute(request())
    goal = result.goals[0]
    assert (goal.termination_reason, goal.mutation_state) == ("blocked", "none")
    assert len(provider.requests) == 3 and adapter.acts == []


def test_an_error_detail_names_the_reason_not_only_the_type():
    """E2E-I27: a bare ValueError told the outer agent nothing."""

    class Broken(FakeAdapter):
        def observe(self):
            raise ValueError("target_id must use screen:<HWND>:<PID>")

    result = WindowsRunCoordinator(adapter_factory=lambda _request: Broken(), decision_provider=ScriptedProvider()).execute(request())
    assert result.goals[0].detail == "ValueError: target_id must use screen:<HWND>:<PID>"


class NoTextHelper:
    def generate(self, context, **_kwargs):
        raise ValueError("Text helper returned no valid field value; nothing typed.")


def test_a_fill_with_nothing_to_type_is_withheld_and_the_provider_chooses_again():
    # E2E-I36: "click the Email field" chose fill; the run used to stop with a text-helper error.
    adapter = FakeAdapter()
    fill = ObservedCandidate("fill", "fill", "Email", "runtime-1")
    focus = ObservedCandidate("focus", "focus", "Email", "runtime-1")
    adapter.observe = lambda: Observation(f"obs-{adapter.semantic}", f"sem-{adapter.semantic}", (fill, focus), True)
    provider = ScriptedProvider("fill", "focus", "DONE")
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=provider, text_helper=NoTextHelper()
    ).execute(request(allowed_operations=("fill", "focus")))

    assert result.goals[0].termination_reason == "provider_done"
    assert adapter.acts == [("focus", None)]
    assert [candidate.id for candidate in provider.requests[1].candidates] == ["focus"]


def test_a_field_already_showing_the_delivered_text_is_not_filled_again():
    # BUG-0061: after the fill landed, Jev re-filled the editor instead of Ctrl+S and spent the two-action budget.
    adapter = FakeAdapter()
    value = 'value = "Finitact 日本語"'
    save = ObservedCandidate("save", "key", "Press Ctrl+S", "key:Ctrl+S")

    def observe():
        label = "finita, / 2 / DLDVALUE / 1 / 2" if not adapter.acts else 'finitact_phase_i.py / v a l u e = " F i n i t a c t 日本語 / 1'
        editor = ObservedCandidate(f"editor-{adapter.semantic}", "fill", label, "runtime-1")
        return Observation(f"obs-{adapter.semantic}", f"sem-{adapter.semantic}", (editor, save), True)

    adapter.observe = observe
    provider = ScriptedProvider("editor-1", "save", "DONE")
    result = WindowsRunCoordinator(adapter_factory=lambda _request: adapter, decision_provider=provider).execute(
        request(allowed_operations=("fill", "key"), fill_values={"g1": value}, action_budget=2)
    )

    assert [candidate.id for candidate in provider.requests[1].candidates] == ["save"]
    assert adapter.acts == [("editor-1", value), ("save", None)]
    assert result.goals[0].termination_reason == "provider_done"


def test_a_short_text_counts_as_shown_only_when_the_field_holds_it_whole():
    from finitact.runs import _shows_text

    assert _shows_text(" 30 ", "30")
    assert not _shows_text("Qty 30", "30")
    assert not _shows_text("finita, / 2 / DLDVALUE / 1 / 2", 'value = "Finitact 日本語"')


class ProgressListAdapter(ListAdapter):
    def scroll_progress(self, before, after, candidate):
        return before.semantic_id != after.semantic_id


def _progress_run(decisions, *, gate=True, adapter=None):
    adapter = adapter or ProgressListAdapter([f"Row {i}" for i in range(6)], blocks=2)
    result = WindowsRunCoordinator(
        adapter_factory=lambda _request: adapter, decision_provider=JudgedProvider(*decisions),
        scroll_progress_gate=gate,
    ).execute(request(allowed_operations=("click", "scroll")))
    return result.goals[0], adapter


def test_scroll_progress_gate_passes_a_low_confidence_scroll_after_progress_in_the_same_area():
    goal, adapter = _progress_run((
        ("down-0", 0.45, {"down-0": 0.5, "BLOCKED": 0.2}),
        ("down-0", 0.32, {"down-0": 0.36, "BLOCKED": 0.3}),
        ("DONE", 0.9, {"DONE": 0.9}),
    ))

    assert adapter.acts == ["down-0", "down-0"]
    assert goal.metrics["scroll_progress_gates"] == 1


def test_scroll_progress_gate_is_off_by_default():
    goal, adapter = _progress_run((
        ("down-0", 0.45, {"down-0": 0.5}),
        ("down-0", 0.32, {"down-0": 0.36}),
    ), gate=False)

    assert goal.termination_reason == "provider_uncertain" and adapter.acts == ["down-0"]


def test_scroll_progress_gate_holds_without_progress_another_area_or_a_higher_alternative():
    at_end = ProgressListAdapter(["Row 0", "Row 1", "Row 2"], blocks=2)
    cases = (
        (at_end, ("down-0", 0.32, {"down-0": 0.36})),
        (None, ("down-1", 0.32, {"down-1": 0.36})),
        (None, ("up-0", 0.32, {"up-0": 0.36})),
        (None, ("down-0", 0.32, {"down-0": 0.3, "BLOCKED": 0.35})),
    )
    for adapter, second in cases:
        goal, used = _progress_run((("down-0", 0.45, {"down-0": 0.5}), second), adapter=adapter)
        assert goal.termination_reason == "provider_uncertain", second
        assert used.acts == ["down-0"]


def test_provider_failure_after_a_confirmed_click_keeps_the_mutation_in_the_goal_result():
    class FailingProvider(ScriptedProvider):
        def decide(self, request, *, attempts, call_id):
            if self.requests:
                raise ValueError("Invalid TypeSafe response; no action executed.")
            return super().decide(request, attempts=attempts, call_id=call_id)

    adapter = FakeAdapter()
    coordinator = WindowsRunCoordinator(adapter_factory=lambda _request: adapter, decision_provider=FailingProvider("save"))

    goal = coordinator.execute(request()).goals[0]

    assert adapter.acts == [("save", None)]
    assert (goal.termination_reason, goal.mutation_state) == ("error", "confirmed")
    assert "Invalid TypeSafe response" in goal.detail
