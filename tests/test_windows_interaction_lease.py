from contextlib import contextmanager

import pytest

from finitact.interaction_lease import (
    ExclusiveInputEnvironment,
    InputEnvironmentState,
    LeaseAbandoned,
    LeaseUnavailable,
)
from finitact.windows_interaction_lease import (
    WAIT_ABANDONED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    OwnInputLedger,
    WindowsIdleTimePrecondition,
    WindowsInputScope,
    ProcessMutexState,
    WindowsNamedInteractionLease,
    WindowsSyntheticInputLease,
    _mutex_name,
)

ENVIRONMENT = ExclusiveInputEnvironment("windows-automation", "g1")
SCOPE = WindowsInputScope(ENVIRONMENT, 4, "WinSta0", "Automation")


class FakeMutexApi:
    def __init__(self, wait_result=WAIT_OBJECT_0, *, release_result=True):
        self.wait_result = wait_result
        self.release_result = release_result
        self.calls = []

    def CreateMutexW(self, attributes, initial_owner, name):
        self.calls.append(("create", name))
        return 42

    def WaitForSingleObject(self, handle, milliseconds):
        self.calls.append(("wait", handle, milliseconds))
        return self.wait_result

    def ReleaseMutex(self, handle):
        self.calls.append(("release", handle))
        return self.release_result

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return True


def test_named_mutex_is_held_until_the_context_exits():
    api = FakeMutexApi()
    lease = WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 5.0, process_state=ProcessMutexState())
    with lease.acquire(ENVIRONMENT, deadline_monotonic=5.25) as grant:
        assert grant.environment == ENVIRONMENT
        assert [call[0] for call in api.calls] == ["create", "wait"]
        assert api.calls[1][2] == 250
    assert [call[0] for call in api.calls] == ["create", "wait", "release"]
    assert api.calls[0][1].startswith("Local\\FinitactSyntheticInput-")


@pytest.mark.parametrize(
    ("wait_result", "error"),
    [(WAIT_TIMEOUT, LeaseUnavailable), (WAIT_ABANDONED, LeaseAbandoned)],
)
def test_failed_acquisition_never_releases_an_unowned_mutex(wait_result, error):
    api = FakeMutexApi(wait_result)
    lease = WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=ProcessMutexState())
    with pytest.raises(error):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pytest.fail("lease must not be granted")
    assert [call[0] for call in api.calls] == ["create", "wait"]


def test_release_failure_is_abandoned_not_success():
    api = FakeMutexApi(release_result=False)
    lease = WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=ProcessMutexState())
    with pytest.raises(LeaseAbandoned):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pass
    assert [call[0] for call in api.calls][-1] == "release"


def test_mutex_handle_is_kept_open_across_acquisitions_so_owner_crashes_stay_observable():
    api = FakeMutexApi()
    lease = WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=ProcessMutexState())
    for _ in range(2):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pass
    assert [call[0] for call in api.calls] == ["create", "wait", "release", "wait", "release"]


@pytest.mark.parametrize(("wait_result", "release_result"), [(WAIT_ABANDONED, True), (WAIT_OBJECT_0, False)])
def test_abandonment_is_sticky_per_desktop_and_refuses_other_refs_without_waiting(wait_result, release_result):
    state = ProcessMutexState()
    api = FakeMutexApi(wait_result, release_result=release_result)
    first = WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=state)
    with pytest.raises(LeaseAbandoned):
        with first.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pass
    calls_before = list(api.calls)

    # A different exclusive_environment_ref on the same desktop shares the mutex name. Waiting again
    # on the thread that gained ownership by abandonment would succeed recursively (BUG-0015).
    other_ref = ExclusiveInputEnvironment("another-ref", "g1")
    api.wait_result, api.release_result = WAIT_OBJECT_0, True
    second = WindowsNamedInteractionLease(
        WindowsInputScope(other_ref, 4, "WinSta0", "Automation"), api=api, clock=lambda: 1.0, process_state=state
    )
    with pytest.raises(LeaseAbandoned, match="earlier in this process"):
        with second.acquire(other_ref, deadline_monotonic=2.0):
            pytest.fail("an abandoned desktop mutex must not be granted again")
    assert api.calls == calls_before
    if wait_result == WAIT_ABANDONED:
        assert [call[0] for call in calls_before] == ["create", "wait"]


def test_abandonment_does_not_block_a_different_desktop():
    state = ProcessMutexState()
    abandoned = FakeMutexApi(WAIT_ABANDONED)
    with pytest.raises(LeaseAbandoned):
        with WindowsNamedInteractionLease(SCOPE, api=abandoned, process_state=state, clock=lambda: 1.0).acquire(
            ENVIRONMENT, deadline_monotonic=2.0
        ):
            pass
    other_desktop = WindowsInputScope(ENVIRONMENT, 4, "WinSta0", "Other")
    healthy = FakeMutexApi()
    with WindowsNamedInteractionLease(other_desktop, api=healthy, process_state=state, clock=lambda: 1.0).acquire(
        ENVIRONMENT, deadline_monotonic=2.0
    ):
        pass
    assert [call[0] for call in healthy.calls] == ["create", "wait", "release"]


def test_startup_keepalive_handle_is_reused_by_the_first_run(monkeypatch):
    import finitact.windows_interaction_lease as module

    monkeypatch.setattr(module, "current_windows_input_scope", lambda environment: SCOPE)
    state = ProcessMutexState()
    api = FakeMutexApi()
    module.keep_current_desktop_mutex_open(api=api, process_state=state)
    with WindowsNamedInteractionLease(SCOPE, api=api, process_state=state, clock=lambda: 1.0).acquire(
        ENVIRONMENT, deadline_monotonic=2.0
    ):
        pass
    assert [call[0] for call in api.calls] == ["create", "wait", "release"]


def test_environment_generation_does_not_split_the_lock_for_the_same_desktop():
    replacement = WindowsInputScope(
        ExclusiveInputEnvironment("windows-automation", "g2"), 4, "WinSta0", "Automation"
    )
    assert _mutex_name(SCOPE) == _mutex_name(replacement)


def test_platform_neutral_environment_must_match_the_windows_scope():
    api = FakeMutexApi()
    lease = WindowsNamedInteractionLease(SCOPE, api=api, process_state=ProcessMutexState())
    different = ExclusiveInputEnvironment("other-environment", "g1")
    with pytest.raises(LeaseUnavailable, match="does not match"):
        with lease.acquire(different, deadline_monotonic=2.0):
            pytest.fail("lease must not be granted")
    assert api.calls == []


class FakeIdleTimeApi:
    def __init__(self, *, tick_count: int, last_input_tick: int) -> None:
        self.tick_count = tick_count
        self.last_input_tick = last_input_tick

    def get_last_input_tick(self) -> int:
        return self.last_input_tick

    def get_tick_count(self) -> int:
        return self.tick_count


def test_idle_time_precondition_is_satisfied_once_the_threshold_elapses():
    api = FakeIdleTimeApi(tick_count=10_000, last_input_tick=4_000)  # 6.0s idle
    precondition = WindowsIdleTimePrecondition(minimum_idle_seconds=5.0, api=api)
    assert precondition.seconds_idle() == pytest.approx(6.0)
    assert precondition.satisfied()


def test_idle_time_precondition_is_not_satisfied_below_the_threshold():
    api = FakeIdleTimeApi(tick_count=10_000, last_input_tick=8_000)  # 2.0s idle
    precondition = WindowsIdleTimePrecondition(minimum_idle_seconds=5.0, api=api)
    assert not precondition.satisfied()


def test_idle_time_precondition_handles_32bit_tick_count_wraparound():
    # GetTickCount wrapped past 2**32 while dwTime (last input) was recorded just before the wrap.
    api = FakeIdleTimeApi(tick_count=500, last_input_tick=(1 << 32) - 1_500)  # 2.0s idle
    precondition = WindowsIdleTimePrecondition(minimum_idle_seconds=1.0, api=api)
    assert precondition.seconds_idle() == pytest.approx(2.0)
    assert precondition.satisfied()


def test_idle_time_precondition_rejects_a_non_positive_threshold():
    with pytest.raises(ValueError):
        WindowsIdleTimePrecondition(minimum_idle_seconds=0, api=FakeIdleTimeApi(tick_count=0, last_input_tick=0))


class FakeIndicator:
    def __init__(self, events):
        self.events = events

    @contextmanager
    def delivering(self):
        self.events.append("indicator-show")
        try:
            yield
        finally:
            self.events.append("indicator-hide")


def test_three_layer_lease_checks_idle_after_mutex_and_wraps_delivery_in_indicator():
    events = []
    api = FakeMutexApi()
    mutex = WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=ProcessMutexState())
    idle = WindowsIdleTimePrecondition(
        minimum_idle_seconds=5.0,
        api=FakeIdleTimeApi(tick_count=10_000, last_input_tick=4_000),
    )
    lease = WindowsSyntheticInputLease(
        mutex=mutex,
        idle_time=idle,
        indicator=FakeIndicator(events),
    )

    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        events.append("delivery")

    assert events == ["indicator-show", "delivery", "indicator-hide"]
    assert [call[0] for call in api.calls] == ["create", "wait"]
    lease.close()
    assert [call[0] for call in api.calls] == ["create", "wait", "release"]


def test_three_layer_lease_denies_below_idle_threshold_without_showing_indicator():
    events = []
    api = FakeMutexApi()
    lease = WindowsSyntheticInputLease(
        mutex=WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=ProcessMutexState()),
        idle_time=WindowsIdleTimePrecondition(
            minimum_idle_seconds=5.0,
            api=FakeIdleTimeApi(tick_count=10_000, last_input_tick=8_000),
        ),
        indicator=FakeIndicator(events),
    )

    with pytest.raises(LeaseUnavailable, match="idle-time precondition"):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pytest.fail("delivery must not start")
    assert events == []
    assert [call[0] for call in api.calls] == ["create", "wait", "release"]


def test_three_layer_lease_does_not_yield_when_indicator_readiness_fails():
    events = []
    api = FakeMutexApi()

    class NotReadyIndicator:
        @contextmanager
        def delivering(self):
            events.append("indicator-readiness-failed")
            raise RuntimeError("indicator readiness timed out")
            yield  # pragma: no cover -- unreachable, keeps this a generator for the Protocol shape

    lease = WindowsSyntheticInputLease(
        mutex=WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=ProcessMutexState()),
        idle_time=WindowsIdleTimePrecondition(
            minimum_idle_seconds=5.0,
            api=FakeIdleTimeApi(tick_count=10_000, last_input_tick=4_000),
        ),
        indicator=NotReadyIndicator(),
    )

    with pytest.raises(RuntimeError, match="readiness timed out"):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pytest.fail("delivery must not start")
    assert events == ["indicator-readiness-failed"]
    lease.close()
    assert [call[0] for call in api.calls] == ["create", "wait", "release"]


class SequencedIdleTimeApi:
    def __init__(self, idle_ms_values):
        self._values = list(idle_ms_values)
        self.reads = 0

    def get_tick_count(self):
        return 100_000

    def get_last_input_tick(self):
        self.reads += 1
        return 100_000 - self._values.pop(0)


def _run_lease(api, idle_api, events, *, clock=lambda: 1.0, environment_state=None):
    return WindowsSyntheticInputLease(
        mutex=WindowsNamedInteractionLease(SCOPE, api=api, clock=lambda: 1.0, process_state=ProcessMutexState()),
        idle_time=WindowsIdleTimePrecondition(minimum_idle_seconds=5.0, api=idle_api),
        indicator=FakeIndicator(events),
        environment_state=environment_state,
        clock=clock,
    )


def test_run_scoped_lease_checks_idle_once_and_holds_the_mutex_across_deliveries():
    events = []
    api = FakeMutexApi()
    # The second value would fail the gate; it must never be read because the run's own
    # first SendInput is what reset idle time (ADR-0012 B2).
    idle_api = SequencedIdleTimeApi([6_000, 0])
    lease = _run_lease(api, idle_api, events)

    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        events.append("delivery-1")
    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        events.append("delivery-2")

    assert idle_api.reads == 1
    assert events == [
        "indicator-show",
        "delivery-1",
        "indicator-hide",
        "indicator-show",
        "delivery-2",
        "indicator-hide",
    ]
    assert [call[0] for call in api.calls] == ["create", "wait"]
    lease.close()
    lease.close()
    assert [call[0] for call in api.calls] == ["create", "wait", "release"]


def test_run_scoped_lease_refuses_a_later_delivery_after_the_run_deadline():
    events = []
    now = [1.0]
    lease = _run_lease(FakeMutexApi(), SequencedIdleTimeApi([6_000]), events, clock=lambda: now[0])
    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        pass
    now[0] = 2.0
    with pytest.raises(LeaseUnavailable, match="deadline"):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pytest.fail("a later delivery must not start after the deadline")
    assert events == ["indicator-show", "indicator-hide"]


def test_run_scoped_lease_refuses_reuse_for_a_different_environment():
    lease = _run_lease(FakeMutexApi(), SequencedIdleTimeApi([6_000]), [])
    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        pass
    with pytest.raises(LeaseUnavailable, match="different input environment"):
        with lease.acquire(ExclusiveInputEnvironment("other", "g1"), deadline_monotonic=2.0):
            pytest.fail("must not reuse the lease for another environment")


def test_run_scoped_lease_release_failure_blocks_the_environment_for_later_runs():
    state = InputEnvironmentState()
    lease = _run_lease(
        FakeMutexApi(release_result=False), SequencedIdleTimeApi([6_000]), [], environment_state=state
    )
    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        pass
    with pytest.raises(LeaseAbandoned):
        lease.close()
    assert "run-scoped lease release failed" in state.blocked_reason(ENVIRONMENT)


def test_two_action_run_checks_idle_once_and_releases_the_mutex_at_run_end():
    from finitact.contracts import Decision
    from finitact.interaction_lease import SyntheticInputGuard, SyntheticInputPolicy
    from finitact.runs import Goal, WindowsRunCoordinator, WindowsRunRequest
    from finitact.screen_grounded_adapter import ScreenGroundedAdapter, VisualRegion, WindowFrame

    def window(hwnd):
        return WindowFrame(hwnd, 7, 4, 3, bytes([0, 0, 0, 255]) * 12)

    class Target:
        popup_open = False

        def capture(self):
            raise AssertionError("scope capture expected")

        def capture_scope(self):
            return (window(100), window(200)) if self.popup_open else (window(100),)

    class Extractor:
        def extract(self, frame):
            label = "Full HD" if frame.hwnd == 200 else "Resolution"
            return (VisualRegion(label.lower(), label, (1, 1, 2, 1), 0.99, f"test:{label}"),)

    target = Target()
    clicks = []

    class Pointer:
        def target_is_valid(self, *, hwnd, process_id, point, **kwargs):
            return True

        def click(self, *, hwnd, process_id, point, **kwargs):
            clicks.append(hwnd)
            target.popup_open = hwnd == 100
            return True

    class Provider:
        def __init__(self):
            self.labels = iter(("Resolution", "Full HD"))

        def decide(self, request, *, attempts, call_id):
            attempts.append({"status": "confirmed", "call_id": call_id})
            wanted = next(self.labels, None)
            match = next((c for c in request.candidates if c.label == wanted), None)
            return Decision(choice=match.id) if match else Decision(terminal_reason="done")

    api = FakeMutexApi()
    idle_api = SequencedIdleTimeApi([6_000, 0, 0])

    def factory(_request):
        lease = _run_lease(api, idle_api, [])
        guard = SyntheticInputGuard(
            policy=SyntheticInputPolicy(True, ENVIRONMENT),
            lease=lease,
            environment_state=InputEnvironmentState(),
            validate_environment=lambda actual: actual == ENVIRONMENT,
        )
        return ScreenGroundedAdapter(
            capture=target, extractor=Extractor(), pointer=Pointer(), guard=guard, deadline_monotonic=2.0
        )

    result = WindowsRunCoordinator(adapter_factory=factory, decision_provider=Provider()).execute(
        WindowsRunRequest(
            run_id="two-action",
            target_id="screen:100:7",
            goals=(Goal("g1", "Select Full HD"),),
            allowed_operations=("click",),
            synthetic_input_allowed=True,
            exclusive_environment_ref="isolated",
            action_budget=2,
            provider_attempt_budget=6,
        )
    )

    assert result.status == "completed"
    assert result.goals[0].mutation_state == "confirmed"
    assert clicks == [100, 200]
    assert idle_api.reads == 1
    assert [call[0] for call in api.calls] == ["create", "wait", "release"]


class MutableIdleTimeApi:
    def __init__(self, *, tick_count: int, last_input_tick: int) -> None:
        self.tick_count = tick_count
        self.last_input_tick = last_input_tick

    def get_last_input_tick(self) -> int:
        return self.last_input_tick

    def get_tick_count(self) -> int:
        return self.tick_count


def _ledger_lease(idle_api, ledger):
    return WindowsSyntheticInputLease(
        mutex=WindowsNamedInteractionLease(SCOPE, api=FakeMutexApi(), clock=lambda: 1.0, process_state=ProcessMutexState()),
        idle_time=WindowsIdleTimePrecondition(minimum_idle_seconds=30.0, api=idle_api, ledger=ledger),
        indicator=FakeIndicator([]),
    )


def _one_run(idle_api, ledger, *, click_tick):
    lease = _ledger_lease(idle_api, ledger)
    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        idle_api.last_input_tick = click_tick
    lease.close()


def test_next_run_measures_idle_from_the_verified_origin_when_only_our_input_followed():
    ledger = OwnInputLedger()
    idle_api = MutableIdleTimeApi(tick_count=100_000, last_input_tick=60_000)  # 40s idle
    _one_run(idle_api, ledger, click_tick=101_000)
    idle_api.tick_count = 110_000  # our click was 9s ago, the human origin 50s ago

    lease = _ledger_lease(idle_api, ledger)
    with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
        pass
    lease.close()


def test_input_after_our_run_resets_the_carried_idle_origin():
    ledger = OwnInputLedger()
    idle_api = MutableIdleTimeApi(tick_count=100_000, last_input_tick=60_000)
    _one_run(idle_api, ledger, click_tick=101_000)
    idle_api.last_input_tick = 105_000  # someone else's input after our run ended
    idle_api.tick_count = 110_000

    lease = _ledger_lease(idle_api, ledger)
    with pytest.raises(LeaseUnavailable, match="idle-time"):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pytest.fail("human input after our run must restart the idle wait")


def test_a_run_denied_for_idle_does_not_record_its_tick_as_ours():
    ledger = OwnInputLedger()
    idle_api = MutableIdleTimeApi(tick_count=100_000, last_input_tick=95_000)  # 5s idle
    lease = _ledger_lease(idle_api, ledger)
    with pytest.raises(LeaseUnavailable):
        with lease.acquire(ENVIRONMENT, deadline_monotonic=2.0):
            pass
    lease.close()
    assert ledger.origin_for(95_000) == 95_000
