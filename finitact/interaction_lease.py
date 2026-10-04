"""Platform-neutral, fail-closed guard for synthetic GUI input (ADR-0006, Phase B).

An OS adapter supplies its lease, environment validation, target re-resolution and delivery
callbacks.  Keeping the guard here fixes ordering and failure semantics without leaking Windows
session concepts into the common contract or pretending to validate native input delivery.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Literal, Protocol

from .action_adapter import MutationResult, MutationUncertain, Observation
from .contracts import ObservedCandidate

SyntheticInputMethod = Literal["synthetic_key", "synthetic_pointer"]


@dataclass(frozen=True)
class ExclusiveInputEnvironment:
    """Runtime-issued guarantee that one identified input environment is mutation-exclusive."""

    identity: str
    generation: str


@dataclass(frozen=True)
class SyntheticInputPolicy:
    allowed: bool = False
    environment: ExclusiveInputEnvironment | None = None


@dataclass(frozen=True)
class LeaseGrant:
    environment: ExclusiveInputEnvironment


class LeaseUnavailable(RuntimeError):
    """No input was sent because the shared input environment could not be leased."""


class LeaseAbandoned(LeaseUnavailable):
    """A previous owner ended without proving its input operation had stopped."""


class DeliveryRefused(RuntimeError):
    """The native call stopped itself at a checked point and reports exactly what it sent.

    The environment stays reusable: unlike an exception mid-delivery, nothing was left half-sent
    (E2E-I1). The goal still ends uncertain because the part that was sent (e.g. the fill's focusing
    click) may have had effects of its own.
    """


class InteractionLease(Protocol):
    def acquire(
        self, environment: ExclusiveInputEnvironment, *, deadline_monotonic: float
    ) -> AbstractContextManager[LeaseGrant]:
        """Acquire until explicitly released; the deadline only bounds acquisition waiting."""


class InputEnvironmentState:
    """In-process view of whether an isolated input environment remains reusable.

    A native environment manager must back the same semantics across processes.  This local
    implementation is only a contract fixture, not evidence of OS-level isolation.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._blocked: dict[ExclusiveInputEnvironment, str] = {}

    def block(self, environment: ExclusiveInputEnvironment, reason: str) -> None:
        with self._lock:
            self._blocked.setdefault(environment, reason)

    def blocked_reason(self, environment: ExclusiveInputEnvironment) -> str | None:
        with self._lock:
            return self._blocked.get(environment)


ResolveTarget = Callable[[Observation, ObservedCandidate], ObservedCandidate | None]
ValidateEnvironment = Callable[[ExclusiveInputEnvironment], bool]
# A str is a refusal with its reason; False a refusal without one.
VerifyDeliveryTarget = Callable[[ObservedCandidate, SyntheticInputMethod], bool | str]
SendSyntheticInput = Callable[[ObservedCandidate, SyntheticInputMethod], bool]


class SyntheticInputGuard:
    """Enforce policy, lease, re-resolution and delivery checks at the adapter boundary."""

    def __init__(
        self,
        *,
        policy: SyntheticInputPolicy,
        lease: InteractionLease,
        environment_state: InputEnvironmentState,
        validate_environment: ValidateEnvironment,
    ) -> None:
        self.policy = policy
        self.lease = lease
        self.environment_state = environment_state
        self.validate_environment = validate_environment

    def execute(
        self,
        *,
        candidate: ObservedCandidate,
        observation: Observation,
        input_method: SyntheticInputMethod,
        deadline_monotonic: float,
        resolve_target: ResolveTarget,
        verify_delivery_target: VerifyDeliveryTarget,
        send: SendSyntheticInput,
    ) -> MutationResult:
        environment = self.policy.environment
        delivery_started = False
        denied = self._denial_reason(environment)
        if denied is not None:
            return MutationResult(candidate.id, input_method, "not_attempted", denied)

        assert environment is not None
        try:
            lease_context = self.lease.acquire(environment, deadline_monotonic=deadline_monotonic)
            with lease_context as grant:
                if grant.environment != environment or not self.validate_environment(environment):
                    return MutationResult(
                        candidate.id, input_method, "not_attempted", "input environment identity is invalid"
                    )
                if reason := self.environment_state.blocked_reason(environment):
                    return MutationResult(
                        candidate.id, input_method, "not_attempted", f"input environment is blocked: {reason}"
                    )

                rebound = resolve_target(observation, candidate)
                if rebound is None:
                    return MutationResult(candidate.id, input_method, "not_attempted", "target is stale or ambiguous")
                if not _same_action(candidate, rebound):
                    return MutationResult(
                        candidate.id, input_method, "not_attempted", "re-resolved target changed action semantics"
                    )
                verdict = verify_delivery_target(rebound, input_method)
                if not verdict or isinstance(verdict, str):
                    reason = "delivery target verification failed"
                    return MutationResult(
                        candidate.id,
                        input_method,
                        "not_attempted",
                        f"{reason}: {verdict}" if isinstance(verdict, str) and verdict else reason,
                    )

                try:
                    delivery_started = True
                    confirmed = send(rebound, input_method)
                except DeliveryRefused as exc:
                    raise MutationUncertain(str(exc)) from exc
                except Exception as exc:
                    self.environment_state.block(environment, f"synthetic input did not finish cleanly: {exc}")
                    raise MutationUncertain("synthetic input may have been partially delivered") from exc
                if not confirmed:
                    self.environment_state.block(environment, "synthetic input outcome was not confirmed")
                    raise MutationUncertain("synthetic input outcome was not confirmed")
                return MutationResult(candidate.id, input_method, "confirmed")
        except LeaseAbandoned as exc:
            self.environment_state.block(environment, str(exc) or "interaction lease was abandoned")
            if delivery_started:
                raise MutationUncertain("interaction lease failed after synthetic input started") from exc
            return MutationResult(candidate.id, input_method, "not_attempted", "interaction lease was abandoned")
        except LeaseUnavailable as exc:
            if delivery_started:
                self.environment_state.block(environment, f"interaction lease failed after delivery started: {exc}")
                raise MutationUncertain("interaction lease failed after synthetic input started") from exc
            return MutationResult(candidate.id, input_method, "not_attempted", str(exc))

    def close(self) -> None:
        close = getattr(self.lease, "close", None)
        if close is not None:
            close()

    def _denial_reason(self, environment: ExclusiveInputEnvironment | None) -> str | None:
        if not self.policy.allowed:
            return "synthetic input is not allowed by run policy"
        if environment is None:
            return "no exclusive input environment guarantee was assigned"
        if reason := self.environment_state.blocked_reason(environment):
            return f"input environment is blocked: {reason}"
        return None


def _same_action(original: ObservedCandidate, rebound: ObservedCandidate) -> bool:
    """Allow runtime-id rebinding without silently changing the requested operation/meaning."""

    return (original.operation, original.label) == (rebound.operation, rebound.label)
