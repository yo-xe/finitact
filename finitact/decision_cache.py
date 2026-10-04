"""Opt-in cache for provider decisions, never for mutations or outcomes.

Stagehand's observe -> deterministic act split demonstrates the value of reusing a prepared
action without another model call. Finitact keeps a stricter boundary: only an exact
DecisionRequest may hit this cache, while adapter freshness, mutation delivery and independent
outcome verification always run again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from threading import Lock
from typing import Any, Mapping, Protocol, Sequence

from .contracts import Decision, DecisionRequest


class DecisionCache(Protocol):
    def get(self, request: DecisionRequest) -> Decision | None: ...

    def put(self, request: DecisionRequest, decision: Decision) -> None: ...


class InMemoryDecisionCache:
    """Process-local exact-request cache for explicitly configured coordinators."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, Decision] = {}

    def get(self, request: DecisionRequest) -> Decision | None:
        with self._lock:
            decision = self._entries.get(decision_request_key(request))
        if decision is None:
            return None
        return replace(
            decision,
            provider_metadata={**decision.provider_metadata, "decision_cache": "HIT"},
        )

    def put(self, request: DecisionRequest, decision: Decision) -> None:
        with self._lock:
            self._entries[decision_request_key(request)] = decision


def decision_request_key(request: DecisionRequest) -> str:
    payload = {
        "goal": request.goal,
        "observation_id": request.observation_id,
        "candidates": [candidate.provider_record() for candidate in request.candidates],
        "untrusted_context": request.untrusted_context,
        "history": request.history,
        "remaining_attempts": request.remaining_attempts,
    }
    if request.goal_inputs:
        # Only when present, so keys cached before goal_inputs existed still match.
        payload["goal_inputs"] = request.goal_inputs
    body = json.dumps(_canonical(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"decision cache requests must contain JSON-like values, got {type(value).__name__}")
