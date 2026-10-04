"""Deterministic sweep of one observed UIA SelectionItem group."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from .action_adapter import ActionAdapter, Freshness, Observation
from .contracts import DecisionProvider, ObservedCandidate, windows_decision_request


@dataclass(frozen=True)
class SweepStep:
    candidate_id: str
    label: str
    status: str
    evidence: Mapping[str, Any]
    detail: str | None = None


@dataclass(frozen=True)
class SelectionSweepResult:
    group_id: str
    steps: tuple[SweepStep, ...]
    completed: bool


EvidenceCollector = Callable[[ObservedCandidate, Observation], Mapping[str, Any]]
GroupPreparer = Callable[[], Observation]


class SelectionSweepCoordinator:
    """Let a decision provider identify one group, then enumerate that group deterministically."""

    def __init__(self, decision_provider: DecisionProvider) -> None:
        self.decision_provider = decision_provider

    def execute(
        self,
        adapter: ActionAdapter,
        observation: Observation,
        goal: str,
        *,
        capture: EvidenceCollector,
        prepare: GroupPreparer | None = None,
        provider_attempt_budget: int = 3,
    ) -> SelectionSweepResult:
        if not observation.complete or provider_attempt_budget <= 0:
            raise ValueError("selection sweep requires a complete observation and positive provider budget")
        group_sizes: dict[str, int] = {}
        for candidate in observation.candidates:
            group = candidate.attributes.get("selection_group")
            if candidate.operation == "select" and group:
                group_sizes[str(group)] = group_sizes.get(str(group), 0) + 1
        candidates = tuple(
            candidate
            for candidate in observation.candidates
            if candidate.operation == "select"
            and group_sizes.get(str(candidate.attributes.get("selection_group")), 0) > 1
        )
        candidate_ids = {candidate.id for candidate in candidates}
        decision_observation = replace(
            observation,
            candidates=candidates,
            untrusted_values={
                candidate_id: value
                for candidate_id, value in observation.untrusted_values.items()
                if candidate_id in candidate_ids
            },
        )
        attempts: list[dict] = []
        decision = self.decision_provider.decide(
            windows_decision_request(decision_observation, goal, (), provider_attempt_budget),
            attempts=attempts,
            call_id="selection-group-1",
        )
        seed = next((candidate for candidate in candidates if candidate.id == decision.choice), None)
        if decision.terminal_reason or seed is None:
            raise ValueError("decision provider did not select an observed multi-value selection group")
        return sweep_selection_group(adapter, observation, seed, capture=capture, prepare=prepare)


def sweep_selection_group(
    adapter: ActionAdapter,
    observation: Observation,
    seed: ObservedCandidate,
    *,
    capture: EvidenceCollector,
    prepare: GroupPreparer | None = None,
) -> SelectionSweepResult:
    """Select every member of ``seed``'s observed group once and capture each resulting state.

    Membership and order are frozen from the initial complete observation. Each member is
    re-resolved by semantic identity immediately before execution; any ambiguity, mutation
    uncertainty, or disappearing group stops the sweep without retrying that member.
    """

    if not observation.complete:
        raise ValueError("selection sweep requires a complete observation")
    group_id = str(seed.attributes.get("selection_group") or "")
    if seed.operation != "select" or not group_id:
        raise ValueError("seed must be a select candidate with a selection_group")
    members = tuple(
        candidate
        for candidate in observation.candidates
        if candidate.operation == "select" and candidate.attributes.get("selection_group") == group_id
    )
    if not members:
        raise ValueError("selection group has no members")

    current = observation
    steps: list[SweepStep] = []
    for wanted in members:
        if prepare is not None:
            current = prepare()
            if not current.complete:
                steps.append(
                    SweepStep(wanted.id, wanted.label, "not_attempted", {}, "prepared observation is incomplete")
                )
                return SelectionSweepResult(group_id, tuple(steps), False)
        matches = [
            candidate
            for candidate in current.candidates
            if _same_member(candidate, wanted, group_id, prepared=prepare is not None)
        ]
        if len(matches) != 1:
            steps.append(SweepStep(wanted.id, wanted.label, "not_attempted", {}, "member is stale or ambiguous"))
            return SelectionSweepResult(group_id, tuple(steps), False)
        candidate = matches[0]
        if adapter.fresh(current, candidate) is not Freshness.FRESH:
            steps.append(SweepStep(wanted.id, wanted.label, "not_attempted", {}, "observation is stale"))
            return SelectionSweepResult(group_id, tuple(steps), False)
        mutation = adapter.act(candidate, current)
        if mutation.status != "confirmed":
            steps.append(SweepStep(wanted.id, wanted.label, mutation.status, {}, mutation.detail))
            return SelectionSweepResult(group_id, tuple(steps), False)
        current = adapter.observe()
        if not current.complete:
            steps.append(SweepStep(wanted.id, wanted.label, "uncertain", {}, "post-mutation observation is incomplete"))
            return SelectionSweepResult(group_id, tuple(steps), False)
        steps.append(SweepStep(wanted.id, wanted.label, "confirmed", dict(capture(candidate, current))))
    return SelectionSweepResult(group_id, tuple(steps), True)


def _same_member(
    candidate: ObservedCandidate, wanted: ObservedCandidate, group_id: str, *, prepared: bool
) -> bool:
    return (
        candidate.operation == "select"
        and bool(candidate.attributes.get("selection_group"))
        and (prepared or candidate.attributes.get("selection_group") == group_id)
        and candidate.label == wanted.label
        and (prepared or candidate.attributes.get("automation_id") == wanted.attributes.get("automation_id"))
    )
