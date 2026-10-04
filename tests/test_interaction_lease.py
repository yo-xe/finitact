from contextlib import contextmanager

import pytest

from finitact.action_adapter import MutationUncertain, Observation
from finitact.contracts import ObservedCandidate
from finitact.interaction_lease import (
    DeliveryRefused,
    ExclusiveInputEnvironment,
    InputEnvironmentState,
    LeaseAbandoned,
    LeaseGrant,
    LeaseUnavailable,
    SyntheticInputGuard,
    SyntheticInputPolicy,
)

ENVIRONMENT = ExclusiveInputEnvironment("test-exclusive-environment", "generation-1")


def candidate(*, id="c1", operation="fill", label="Editor"):
    return ObservedCandidate(id=id, operation=operation, label=label, subject="runtime-1")


def observation(item=None):
    item = item or candidate()
    return Observation("obs-1", "sem-1", (item,), True)


class FakeLease:
    def __init__(self, failure=None):
        self.failure = failure
        self.acquired = 0
        self.released = 0

    @contextmanager
    def acquire(self, environment, *, deadline_monotonic):
        if self.failure:
            raise self.failure
        self.acquired += 1
        try:
            yield LeaseGrant(environment)
        finally:
            self.released += 1


def guard(*, allowed=True, environment=ENVIRONMENT, lease=None, state=None, valid=True):
    return SyntheticInputGuard(
        policy=SyntheticInputPolicy(allowed, environment),
        lease=lease or FakeLease(),
        environment_state=state or InputEnvironmentState(),
        validate_environment=lambda actual: valid and actual == ENVIRONMENT,
    )


def execute(
    subject,
    *,
    send=lambda target, method: True,
    resolve=lambda old, item: item,
    verify=lambda item, method: True,
):
    return subject.execute(
        candidate=candidate(),
        observation=observation(),
        input_method="synthetic_key",
        deadline_monotonic=10.0,
        resolve_target=resolve,
        verify_delivery_target=verify,
        send=send,
    )


@pytest.mark.parametrize(
    ("subject", "detail"),
    [
        (guard(allowed=False), "not allowed"),
        (guard(environment=None), "no exclusive"),
        (guard(valid=False), "identity is invalid"),
    ],
)
def test_missing_policy_or_environment_guarantee_sends_nothing(subject, detail):
    sent = []
    result = execute(subject, send=lambda target, method: sent.append(target))
    assert result.status == "not_attempted"
    assert detail in result.detail
    assert sent == []


def test_lease_timeout_sends_nothing_and_does_not_release_an_unacquired_lease():
    lease = FakeLease(LeaseUnavailable("lease acquisition timed out"))
    result = execute(guard(lease=lease))
    assert result.status == "not_attempted"
    assert lease.acquired == lease.released == 0


def test_target_is_re_resolved_after_acquisition_and_semantic_change_is_rejected():
    lease = FakeLease()
    sent = []
    result = execute(
        guard(lease=lease),
        resolve=lambda old, item: candidate(id="c2", operation="click", label="Other"),
        send=lambda target, method: sent.append(target),
    )
    assert result.status == "not_attempted"
    assert "changed action semantics" in result.detail
    assert sent == []
    assert lease.acquired == lease.released == 1


@pytest.mark.parametrize("method", ["synthetic_key", "synthetic_pointer"])
def test_method_specific_delivery_verification_runs_before_send(method):
    checked = []
    sent = []
    subject = guard()
    result = subject.execute(
        candidate=candidate(),
        observation=observation(),
        input_method=method,
        deadline_monotonic=10.0,
        resolve_target=lambda old, item: item,
        verify_delivery_target=lambda item, actual: checked.append(actual) or False,
        send=lambda target, actual: sent.append(actual),
    )
    assert result.status == "not_attempted"
    assert checked == [method]
    assert sent == []


def test_send_failure_is_uncertain_blocks_environment_and_prevents_a_second_run():
    state = InputEnvironmentState()
    subject = guard(state=state)

    with pytest.raises(MutationUncertain):
        execute(subject, send=lambda target, method: (_ for _ in ()).throw(TimeoutError("still running")))

    sent = []
    result = execute(subject, send=lambda target, method: sent.append(target) or True)
    assert result.status == "not_attempted"
    assert "blocked" in result.detail
    assert sent == []


def test_refused_delivery_is_uncertain_but_leaves_the_environment_usable():
    """E2E-I1: a native stop at a checked point sent nothing half-way, so later runs may still deliver."""
    subject = guard(state=InputEnvironmentState())

    with pytest.raises(MutationUncertain, match="caret stayed"):
        execute(subject, send=lambda target, method: (_ for _ in ()).throw(DeliveryRefused("caret stayed")))

    assert execute(subject, send=lambda target, method: True).status == "confirmed"


def test_unconfirmed_send_is_uncertain_not_an_automatic_retry():
    state = InputEnvironmentState()
    subject = guard(state=state)
    calls = []
    with pytest.raises(MutationUncertain):
        execute(subject, send=lambda target, method: calls.append(target) and False)
    assert len(calls) == 1


def test_abandoned_lease_blocks_environment_without_sending():
    state = InputEnvironmentState()
    subject = guard(lease=FakeLease(LeaseAbandoned("previous owner died")), state=state)
    result = execute(subject)
    assert result.status == "not_attempted"
    assert state.blocked_reason(ENVIRONMENT) == "previous owner died"


def test_success_is_confirmed_and_releases_the_lease():
    lease = FakeLease()
    result = execute(guard(lease=lease))
    assert result.status == "confirmed"
    assert lease.acquired == lease.released == 1


def test_lease_release_failure_after_send_is_uncertain_and_blocks_environment():
    class ReleaseFailureLease(FakeLease):
        @contextmanager
        def acquire(self, environment, *, deadline_monotonic):
            yield LeaseGrant(environment)
            raise LeaseAbandoned("release failed")

    state = InputEnvironmentState()
    subject = guard(lease=ReleaseFailureLease(), state=state)
    with pytest.raises(MutationUncertain):
        execute(subject)
    assert state.blocked_reason(ENVIRONMENT) == "release failed"
