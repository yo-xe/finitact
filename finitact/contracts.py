"""Provider-neutral contracts for bounded action selection and text generation."""

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


# Evidence source of shape-only screen candidates: their label is a synthetic size tag, not on-screen text.
EDGE_CONTOUR_SOURCE = "edge_contour"
FILL_PANEL_SOURCE = "fill_panel"


@dataclass(frozen=True)
class ObservedCandidate:
    id: str
    operation: str
    label: str
    subject: str | None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    # Adapter-only re-identification data (e.g. pixel hashes): never shown to providers, where it
    # was ~80% of a screen observation's payload and carries no meaning for the choice.
    binding: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_action(cls, action):
        return cls(
            id=action["id"],
            operation=action["kind"],
            label=action["label"],
            subject=str(action["node"]) if action.get("node") is not None else None,
            attributes={
                key: value
                for key, value in action.items()
                if key not in {"id", "kind", "label", "node", "rect"}
            },
        )

    def provider_record(self):
        record = {
            "id": self.id,
            "kind": self.operation,
            "label": self.label,
            **self.attributes,
        }
        if self.subject is not None:
            record["node"] = self.subject
        return record


@dataclass(frozen=True)
class DecisionRequest:
    goal: str
    observation_id: str
    candidates: Sequence[ObservedCandidate]
    untrusted_context: Mapping[str, Any]
    history: Sequence[Mapping[str, Any]]
    remaining_attempts: int
    # Caller-given values for this goal (e.g. fill_value, ADR-0026); unlike untrusted_context, not screen data.
    goal_inputs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    choice: str | None = None
    terminal_reason: str | None = None
    measurements: Mapping[str, Any] = field(default_factory=dict)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if (self.choice is None) == (self.terminal_reason is None):
            raise ValueError("Decision must contain exactly one choice or terminal reason")

    def state_record(self):
        selected = self.choice or self.terminal_reason.upper()
        return {
            "choice": selected,
            "terminal_reason": self.terminal_reason,
            "measurements": dict(self.measurements),
            "provider_metadata": dict(self.provider_metadata),
            # Compatibility fields are optional metadata, never common-contract requirements.
            **dict(self.provider_metadata),
            "latency_ms": self.measurements.get("latency_ms", 0),
            "usage": self.measurements.get("usage", {}),
        }


class DecisionProvider(Protocol):
    def decide(self, request: DecisionRequest, *, attempts: list, call_id: str) -> Decision: ...


class TextHelper(Protocol):
    def generate(self, context: Mapping[str, Any], *, attempts: list, call_id: str, attempt_limit: int): ...


def decision_request(page, goal, history, remaining_attempts):
    return DecisionRequest(
        goal=goal,
        observation_id=page["fingerprint"],
        candidates=tuple(ObservedCandidate.from_action(action) for action in page["actions"]),
        untrusted_context={
            key: page.get(key)
            for key in ("url", "title", "text", "unsupported")
            if key in page
        },
        history=tuple(history[-10:]),
        remaining_attempts=remaining_attempts,
    )


# The screen adapter names a scroll by its first and last OCR rows. Replayed in history, the last
# row echoed a current click candidate and drew the next choice onto it (wrong-row-structure-20261003).
_SCROLL_ENDPOINTS = re.compile(r"Scroll (up|down) the list from '.*' to '.*'", re.DOTALL)


def _provider_history(history):
    projected = []
    for item in history:
        match = item.get("kind") == "scroll" and _SCROLL_ENDPOINTS.fullmatch(item.get("action") or "")
        projected.append({**item, "action": f"Scroll {match.group(1)} the observed list"} if match else item)
    return tuple(projected)


def windows_decision_request(observation, goal, history, remaining_attempts, goal_inputs=None):
    """Build the provider-neutral request for one complete GUI adapter observation.

    Imported lazily by shape rather than importing ``Observation`` here, keeping the common
    provider contracts independent of a specific GUI adapter module.
    """

    if not observation.complete:
        raise ValueError("An incomplete observation cannot be sent to a decision provider")
    return DecisionRequest(
        goal=goal,
        observation_id=observation.observation_id,
        candidates=tuple(observation.candidates),
        untrusted_context={"control_values": dict(observation.untrusted_values)},
        history=_provider_history(history[-10:]),
        remaining_attempts=remaining_attempts,
        goal_inputs=dict(goal_inputs or {}),
    )
