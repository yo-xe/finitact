"""ADR-0014 external outcome oracle: judges a run from outside the system under test.

The system under test (Finitact's MCP server or windows-mcp behind an outer agent) is driven by an
opaque callable. Its own result is stored verbatim next to -- never merged into -- the external
verdict, so ``unverified`` from the public server stays ``unverified`` in the record.

Two-action cases can open and close a popup within one run, which before/after snapshots cannot
show (ADR-0014 decision 5). A polling thread therefore records every *distinct* oracle state with
its time from the baseline; the case's judge decides from that transition list and must answer
``undetermined`` rather than infer a transition it never saw.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

SUCCESS = "success"
FAILURE = "failure"
UNDETERMINED = "undetermined"


class TargetMismatch(Exception):
    """The resolved target is gone or now belongs to someone else; nothing about it can be judged."""


@dataclass(frozen=True)
class Sample:
    elapsed_ms: int
    state: Mapping[str, Any]


@dataclass(frozen=True)
class Judgement:
    verdict: str
    detail: str

    def __post_init__(self) -> None:
        if self.verdict not in {SUCCESS, FAILURE, UNDETERMINED}:
            raise ValueError(f"unknown verdict: {self.verdict}")


class CaseOracle(Protocol):
    def snapshot(self) -> Mapping[str, Any]:
        """JSON-serializable target-specific state; raises ``TargetMismatch`` when unbound."""

    def initial_state_violation(self, baseline: Mapping[str, Any]) -> str | None:
        """``None`` when the registered initial_state holds, otherwise why it does not."""

    def judge(self, transitions: Sequence[Sample]) -> Judgement:
        """``transitions[0]`` is the baseline and ``transitions[-1]`` the post-run state."""


def evaluate_run(
    *,
    case_id: str,
    run_id: str,
    target: Mapping[str, Any],
    oracle: CaseOracle,
    drive: Callable[[], Any],
    poll_interval_s: float = 0.05,
    settle_s: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    record: dict[str, Any] = {"case_id": case_id, "run_id": run_id, "target": dict(target)}
    started = clock()

    def elapsed_ms() -> int:
        return round((clock() - started) * 1000)

    try:
        baseline = oracle.snapshot()
    except TargetMismatch as exc:
        return _undetermined(record, driven=False, detail=f"target mismatch before run: {exc}")
    record["baseline"] = dict(baseline)
    violation = oracle.initial_state_violation(baseline)
    record["initial_state"] = {"holds": violation is None, "detail": violation}
    if violation is not None:
        # Driving anyway would spend a trial on a run that ADR-0014 excludes from the denominator.
        return _undetermined(record, driven=False, detail=f"initial_state not met: {violation}")

    transitions = [Sample(0, baseline)]
    poll_errors: list[dict[str, Any]] = []
    poll_times: list[int] = [0]
    lock = threading.Lock()
    stop = threading.Event()

    def observe() -> None:
        try:
            state = oracle.snapshot()
        except Exception as exc:  # noqa: BLE001 - every failure is evidence, not a crash of the runner
            with lock:
                poll_errors.append({"elapsed_ms": elapsed_ms(), "error": type(exc).__name__, "detail": str(exc)})
            return
        now = elapsed_ms()
        with lock:
            poll_times.append(now)
            if state != transitions[-1].state:
                transitions.append(Sample(now, state))

    def poll() -> None:
        while not stop.wait(poll_interval_s):
            observe()

    poller = threading.Thread(target=poll, name="finitact-oracle-poll", daemon=True)
    poller.start()
    drive_started = elapsed_ms()
    try:
        record["system_result"] = drive()
        record["drive_error"] = None
    except Exception as exc:  # noqa: BLE001 - the external outcome is judged even if the driver fails
        record["system_result"] = None
        record["drive_error"] = {"error": type(exc).__name__, "detail": str(exc)}
    finally:
        drive_ended = elapsed_ms()
        # A driver can return before the target applies its last input (Blender's workspace switch
        # lands ~100 ms later); keep polling so the post-run state is the settled one.
        stop.wait(settle_s)
        stop.set()
        poller.join()
    observe()

    record["driver_wall_ms"] = drive_ended - drive_started
    record["transitions"] = [{"elapsed_ms": sample.elapsed_ms, "state": dict(sample.state)} for sample in transitions]
    record["polling"] = {
        "interval_ms": round(poll_interval_s * 1000),
        "samples": len(poll_times),
        "max_gap_ms": max((b - a for a, b in zip(poll_times, poll_times[1:])), default=None),
        "errors": poll_errors,
    }
    if any(error["error"] == TargetMismatch.__name__ for error in poll_errors):
        return _undetermined(record, driven=True, detail="target mismatch during run")
    if poll_times[-1] < drive_ended:
        return _undetermined(record, driven=True, detail="no post-run state could be read")
    judgement = oracle.judge(tuple(transitions))
    record["external"] = {"verdict": judgement.verdict, "detail": judgement.detail, "driven": True}
    return record


def _undetermined(record: dict[str, Any], *, driven: bool, detail: str) -> dict[str, Any]:
    record["external"] = {"verdict": UNDETERMINED, "detail": detail, "driven": driven}
    return record
