import pytest

from finitact.contracts import Decision, DecisionRequest, ObservedCandidate
from finitact.decision_cache import InMemoryDecisionCache, decision_request_key


def request(*, observation_id="obs-1", label="Save"):
    return DecisionRequest(
        goal="Save the document",
        observation_id=observation_id,
        candidates=(ObservedCandidate("save", "click", label, "runtime-1", {"patterns": ("Invoke",)}),),
        untrusted_context={"control_values": {}},
        history=(),
        remaining_attempts=10,
    )


def test_exact_request_returns_cached_decision_with_hit_metadata():
    cache = InMemoryDecisionCache()
    wanted = request()
    cache.put(wanted, Decision(choice="save", provider_metadata={"model": "fixture"}))

    cached = cache.get(wanted)

    assert cached == Decision(
        choice="save",
        provider_metadata={"model": "fixture", "decision_cache": "HIT"},
    )


def test_observation_or_candidate_change_invalidates_the_cache():
    cache = InMemoryDecisionCache()
    cache.put(request(), Decision(choice="save"))

    assert cache.get(request(observation_id="obs-2")) is None
    assert cache.get(request(label="Save As")) is None


def test_cache_key_rejects_non_json_like_candidate_evidence():
    invalid = DecisionRequest(
        goal="g",
        observation_id="o",
        candidates=(ObservedCandidate("x", "click", "X", None, {"opaque": object()}),),
        untrusted_context={},
        history=(),
        remaining_attempts=1,
    )

    with pytest.raises(TypeError, match="JSON-like"):
        decision_request_key(invalid)
