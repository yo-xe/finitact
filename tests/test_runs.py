"""Offline run and MCP contracts. No browser or paid provider calls."""

from copy import deepcopy

import pytest

from finitact.runs import Goal, RunCoordinator, RunRequest, RunResult


class ScriptedAgent:
    scripts = {}
    instances = []

    def __init__(self, url, goal, **_kwargs):
        self.url = url
        self.goal = goal
        self.script = iter(deepcopy(self.scripts[goal]))
        self.current = None
        self.closed = False
        self.state = {
            "page": {"url": url, "fingerprint": "page-1", "actions": []},
            "status": "ready",
            "mutation_attempts": [],
        }
        self.instances.append(self)

    def continue_with(self, goal):
        self.goal = goal
        self.script = iter(deepcopy(self.scripts[goal]))
        self.state["status"] = "ready"

    def command(self, name, _body):
        if name == "predict":
            self.current = next(self.script)
            if self.current.get("on_predict"):
                self.current["on_predict"]()
            if self.current.get("raise_on_predict"):
                raise ValueError(self.current["raise_on_predict"])
            choice = self.current.get("choice", "DONE")
            actions = [] if choice in {"DONE", "BLOCKED"} else [
                {"id": choice, "kind": self.current.get("kind", "click"), "label": "Action"}
            ]
            self.state["page"]["actions"] = actions
            return {"decision": {"choice": choice}}
        if self.current.get("mutation"):
            self.state["mutation_attempts"].append({"status": self.current["mutation"]})
        if self.current.get("raise_on_act"):
            raise RuntimeError(self.current["raise_on_act"])
        if self.current.get("next_url"):
            self.state["page"]["url"] = self.current["next_url"]
        choice = self.current.get("choice", "DONE")
        self.state["status"] = "done" if choice == "DONE" else "blocked" if choice == "BLOCKED" else "ready"
        return self.snapshot()

    def snapshot(self):
        return {"goal": self.goal, **self.state}

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_agent():
    ScriptedAgent.scripts = {}
    ScriptedAgent.instances = []


def request(*goals, run_id="run-1", **overrides):
    values = {
        "run_id": run_id,
        "start_url": "https://fixture.test/start",
        "goals": tuple(Goal(f"g{i}", goal) for i, goal in enumerate(goals, 1)),
        "allowed_origins": ("https://fixture.test",),
    }
    values.update(overrides)
    return RunRequest(**values)


def test_single_goal_separates_provider_done_mutation_and_unverified_outcome():
    ScriptedAgent.scripts = {"submit": [{"choice": "save", "mutation": "confirmed"}, {"choice": "DONE"}]}
    result = RunCoordinator(agent_factory=ScriptedAgent).execute(request("submit"))

    assert result.status == "completed"
    assert result.goals[0].termination_reason == "provider_done"
    assert result.goals[0].mutation_state == "confirmed"
    assert result.goals[0].outcome == "unverified"


def test_ordered_goals_preserve_partial_result_and_session():
    ScriptedAgent.scripts = {"first": [{"choice": "DONE"}], "second": [{"choice": "BLOCKED"}], "third": []}
    result = RunCoordinator(agent_factory=ScriptedAgent).execute(request("first", "second", "third"))

    assert result.status == "partial"
    assert [item.termination_reason for item in result.goals] == ["provider_done", "provider_blocked"]
    assert result.remaining_goal_ids == ("g3",)
    assert len(ScriptedAgent.instances) == 1


def test_same_run_request_replays_cached_result_without_another_mutation():
    ScriptedAgent.scripts = {"submit": [{"choice": "save", "mutation": "confirmed"}, {"choice": "DONE"}]}
    coordinator = RunCoordinator(agent_factory=ScriptedAgent)
    original = coordinator.execute(request("submit"))
    replay = coordinator.execute(request("submit"))

    assert not original.replayed and replay.replayed
    assert len(ScriptedAgent.instances) == 1
    assert len(ScriptedAgent.instances[0].state["mutation_attempts"]) == 1


def test_completed_run_replays_after_coordinator_restart(tmp_path):
    ScriptedAgent.scripts = {"submit": [{"choice": "save", "mutation": "confirmed"}, {"choice": "DONE"}]}
    ledger = tmp_path / "runs.sqlite3"
    RunCoordinator(agent_factory=ScriptedAgent, ledger_path=ledger).execute(request("submit"))
    replay = RunCoordinator(agent_factory=ScriptedAgent, ledger_path=ledger).execute(request("submit"))

    assert replay.replayed
    assert replay.goals[0].termination_reason == "provider_done"
    assert len(ScriptedAgent.instances) == 1


def test_restarted_browser_ledger_keeps_tab_and_final_state(tmp_path):
    ledger = tmp_path / "runs.sqlite3"
    original = RunCoordinator(agent_factory=ScriptedAgent, ledger_path=ledger)
    wanted = request("submit")
    original._save_entry(wanted.run_id, wanted.fingerprint(), "finished", RunResult(
        wanted.run_id, "completed", (), (), "finitact://runs/submit", tab_id="T1",
        final_state={"url": "https://example.com/", "fields": ["SQS = 42500"]},
    ))
    replay = RunCoordinator(agent_factory=ScriptedAgent, ledger_path=ledger).execute(wanted)
    assert replay.replayed and replay.tab_id == "T1"
    assert replay.final_state["fields"] == ["SQS = 42500"]


def test_interrupted_persistent_run_is_never_automatically_restarted(tmp_path):
    ledger = tmp_path / "runs.sqlite3"
    coordinator = RunCoordinator(agent_factory=ScriptedAgent, ledger_path=ledger)
    wanted = request("submit")
    coordinator._save_entry(wanted.run_id, wanted.fingerprint(), "running", None)

    replacement = RunCoordinator(agent_factory=ScriptedAgent, ledger_path=ledger)
    with pytest.raises(RuntimeError, match="new run_id"):
        replacement.execute(wanted)
    assert ScriptedAgent.instances == []
    assert replacement.journal(wanted.run_id)["status"] == "interrupted"
    assert replacement.cancel(wanted.run_id) is False


def test_reusing_run_id_for_different_input_is_rejected():
    ScriptedAgent.scripts = {"first": [{"choice": "DONE"}]}
    coordinator = RunCoordinator(agent_factory=ScriptedAgent)
    coordinator.execute(request("first"))
    with pytest.raises(ValueError, match="different request"):
        coordinator.execute(request("other"))


def test_uncertain_mutation_stops_and_is_not_retried_on_replay():
    ScriptedAgent.scripts = {
        "submit": [
            {"choice": "first", "mutation": "confirmed"},
            {"choice": "save", "mutation": "uncertain", "raise_on_act": "lost"},
        ]
    }
    coordinator = RunCoordinator(agent_factory=ScriptedAgent)
    result = coordinator.execute(request("submit"))
    replay = coordinator.execute(request("submit"))

    assert result.goals[0].termination_reason == "error"
    assert result.goals[0].mutation_state == "uncertain"
    assert "uncertain mutation" in result.goals[0].detail
    assert replay.replayed and len(ScriptedAgent.instances) == 1


def test_deadline_and_cancellation_stop_before_provider_or_mutation():
    ScriptedAgent.scripts = {"wait": [{"choice": "DONE"}]}
    ticks = iter([0.0, 1.0])
    deadline = RunCoordinator(agent_factory=ScriptedAgent, clock=lambda: next(ticks)).execute(
        request("wait", deadline_ms=1)
    )
    assert deadline.goals[0].termination_reason == "deadline"

    coordinator = None

    def cancelling_factory(url, goal, **kwargs):
        agent = ScriptedAgent(url, goal, **kwargs)
        coordinator.cancel("cancelled-run")
        return agent

    coordinator = RunCoordinator(agent_factory=cancelling_factory)
    cancelled = coordinator.execute(request("wait", run_id="cancelled-run"))
    assert cancelled.goals[0].termination_reason == "cancelled"

    coordinator = RunCoordinator(agent_factory=ScriptedAgent)
    ScriptedAgent.scripts = {
        "wait": [{"choice": "save", "mutation": "confirmed", "on_predict": lambda: coordinator.cancel("mid-run")}]
    }
    mid_run = coordinator.execute(request("wait", run_id="mid-run"))
    assert mid_run.goals[0].termination_reason == "cancelled"
    assert mid_run.goals[0].mutation_state == "none"


def test_scope_and_denied_operation_are_enforced_outside_provider():
    ScriptedAgent.scripts = {
        "leave": [{"choice": "link", "mutation": "confirmed", "next_url": "https://outside.test/"}],
        "type": [{"choice": "field", "kind": "fill"}],
    }
    outside = RunCoordinator(agent_factory=ScriptedAgent).execute(request("leave"))
    denied = RunCoordinator(agent_factory=ScriptedAgent).execute(
        request("type", run_id="run-2", denied_operations=("fill",))
    )

    assert outside.goals[0].termination_reason == "blocked"
    assert "outside allowed_origins" in outside.goals[0].detail
    assert denied.goals[0].termination_reason == "blocked"
    assert denied.goals[0].mutation_state == "none"


def test_task_verifier_is_independent_of_provider_done():
    ScriptedAgent.scripts = {"check": [{"choice": "DONE"}]}
    success = RunCoordinator(agent_factory=ScriptedAgent, verifier=lambda _goal, _state: True).execute(
        request("check")
    )
    assert success.goals[0].outcome == "verified_success"


def test_budget_failure_has_explicit_termination_reason():
    ScriptedAgent.scripts = {"loop": [{"raise_on_predict": "Reached provider-attempt budget"}]}
    result = RunCoordinator(agent_factory=ScriptedAgent).execute(request("loop"))
    assert result.goals[0].termination_reason == "budget"


def test_journal_is_redacted_and_referenced_from_results():
    ScriptedAgent.scripts = {"secret goal": [{"choice": "DONE"}]}
    coordinator = RunCoordinator(agent_factory=ScriptedAgent)
    result = coordinator.execute(request("secret goal"))
    journal = coordinator.journal("run-1")

    assert result.journal_ref == "finitact://runs/run-1"
    assert journal["events"] == [
        {"goal_id": "g1", "termination_reason": "provider_done", "detail": None}
    ]
    assert "secret goal" not in str(journal)
