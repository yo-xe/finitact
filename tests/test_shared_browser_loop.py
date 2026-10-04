"""ADR-0037 step 2: the browser path through the shared loop keeps the browser's contracts (consult 20260925-2218)."""

from finitact.browser import MutationUncertain as BrowserMutationUncertain
from finitact.browser import StalePage
from finitact.browser_adapter import BrowserAdapter
from finitact.contracts import Decision
from finitact.runs import Goal, RunCoordinator, RunRequest, Until, WindowsRunCoordinator

ORIGIN = "https://example.test"


def page(n, url=f"{ORIGIN}/a", kinds=("click",)):
    return {
        "fingerprint": f"p{n}", "semantic_fingerprint": f"s{n}", "url": url, "title": "t", "text": "x",
        "actions": [{"id": f"a{i}", "kind": kind, "label": f"{kind} {i}", "node": i} for i, kind in enumerate(kinds)],
    }  # fmt: skip


class Browser:
    def __init__(self, pages, errors=()):
        self.pages, self.errors, self.acted, self.closed = list(pages), list(errors), [], False

    def observe(self, screenshot=True):
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def fresh(self, page, action=None):
        return True

    def act(self, action, page, text=None):
        if self.errors:
            error = self.errors.pop(0)
            if error:
                raise error
        self.acted.append(action["id"])

    def close(self):
        self.closed = True


class Provider:
    def __init__(self, *choices, on_decide=None):
        self.choices, self.on_decide, self.requests = iter(choices), on_decide, []

    def decide(self, request, *, attempts, call_id):
        self.requests.append(request)
        attempts.append({"status": "confirmed"})
        if self.on_decide:
            self.on_decide()
        choice = next(self.choices)
        return Decision(terminal_reason=choice.lower()) if choice in {"DONE", "BLOCKED"} else Decision(choice=choice)


def coordinator(browser, provider, action_verifier=None):
    def shared(request):
        return WindowsRunCoordinator(
            adapter_factory=lambda _r: BrowserAdapter(browser, allowed_origins=request.allowed_origins),
            decision_provider=provider,
            action_verifier=action_verifier,
        )

    return RunCoordinator(shared_loop=shared)


def run(browser, provider, goals=("Save it",), action_verifier=None, **overrides):
    values = {"run_id": "b1", "start_url": f"{ORIGIN}/a", "goals": tuple(Goal(f"g{i}", g) for i, g in enumerate(goals, 1)),
              "allowed_origins": (ORIGIN,)}  # fmt: skip
    values.update(overrides)
    subject = coordinator(browser, provider, action_verifier)
    return subject, subject.execute(RunRequest(**values))


def test_done_after_a_click_is_provider_done_and_unverified_with_the_browser_request_shape():
    browser = Browser([page(1), page(2)])
    provider = Provider("a0", "DONE")
    _, result = run(browser, provider)
    goal = result.goals[0]
    assert (goal.termination_reason, goal.outcome, goal.mutation_state) == ("provider_done", "unverified", "confirmed")
    assert browser.acted == ["a0"] and browser.closed
    assert "control_values" not in provider.requests[0].untrusted_context


def test_stale_before_input_chooses_again_instead_of_blocking():
    browser = Browser([page(1), page(2), page(3)], errors=[StalePage("moved"), None])
    _, result = run(browser, Provider("a0", "a0", "DONE"))
    assert result.goals[0].termination_reason == "provider_done"
    assert browser.acted == ["a0"]


def test_unknown_select_result_stops_as_uncertain():
    browser = Browser([page(1, kinds=("select",))], errors=[BrowserMutationUncertain("lost")])
    _, result = run(browser, Provider("a0"))
    assert (result.goals[0].termination_reason, result.goals[0].mutation_state) == ("error", "uncertain")


def test_wait_is_not_a_mutation():
    browser = Browser([page(1, kinds=("wait",)), page(1, kinds=("wait",))])
    _, result = run(browser, Provider("a0", "a0", "a0", "DONE"))
    assert (result.goals[0].termination_reason, result.goals[0].mutation_state) == ("provider_done", "none")


def test_leaving_the_allowed_origin_blocks_before_deciding():
    browser = Browser([page(1), page(2, url="https://other.test/")])
    provider = Provider("a0", "DONE")
    _, result = run(browser, provider)
    assert (result.goals[0].termination_reason, result.goals[0].detail) == (
        "blocked",
        "page origin is outside allowed_origins",
    )
    assert len(provider.requests) == 1


def test_cancel_while_the_provider_decides_stops_before_input():
    browser = Browser([page(1)])
    holder = {}
    provider = Provider("a0", on_decide=lambda: holder["subject"].cancel("b1"))
    subject = coordinator(browser, provider)
    holder["subject"] = subject
    result = subject.execute(RunRequest("b1", f"{ORIGIN}/a", (Goal("g1", "Save it"),), (ORIGIN,)))
    assert result.goals[0].termination_reason == "cancelled" and browser.acted == []


def test_provider_budget_spans_the_run_goals():
    browser = Browser([page(1), page(2), page(3)])
    _, result = run(browser, Provider("a0", "DONE", "a0"), goals=("First", "Second"), provider_attempt_budget=2)
    assert [goal.termination_reason for goal in result.goals] == ["provider_done", "budget"]


def test_denied_operation_is_not_offered_and_replay_does_not_act_twice():
    browser = Browser([page(1, kinds=("click", "fill")), page(2)])
    provider = Provider("a0", "DONE")
    subject, first = run(browser, provider, denied_operations=("fill",))
    assert [candidate.id for candidate in provider.requests[0].candidates] == ["a0"]
    replay = subject.execute(
        RunRequest("b1", f"{ORIGIN}/a", (Goal("g1", "Save it"),), (ORIGIN,), denied_operations=("fill",))
    )
    assert replay.replayed and browser.acted == ["a0"]


def test_a_kept_tab_is_returned_and_a_later_run_continues_in_it():
    """E2E-I15: runs split by the outer agent restarted the estimate in a fresh tab each time."""
    opened = []

    def shared(request):
        loop = WindowsRunCoordinator(
            adapter_factory=lambda _r: BrowserAdapter(browser, allowed_origins=request.allowed_origins),
            decision_provider=Provider("a0", "DONE", "a0", "DONE"),
        )
        loop.browser_tab = request.tab_id or "tab-1"
        opened.append((request.tab_id, request.keep_tab))
        return loop

    browser = Browser([page(1), page(2), page(3), page(4)])
    subject = RunCoordinator(shared_loop=shared)
    first = subject.execute(RunRequest("k1", f"{ORIGIN}/a", (Goal("g1", "One"),), (ORIGIN,), keep_tab=True))
    assert first.tab_id == "tab-1" and first.record()["tab_id"] == "tab-1"
    second = subject.execute(RunRequest("k2", f"{ORIGIN}/a", (Goal("g1", "Two"),), (ORIGIN,), tab_id="tab-1"))
    assert second.tab_id == "tab-1" and opened == [(None, True), ("tab-1", False)]
    plain = subject.execute(RunRequest("k3", f"{ORIGIN}/a", (Goal("g1", "Three"),), (ORIGIN,)))
    assert "tab_id" not in plain.record()


def test_fill_values_reach_the_shared_loop_without_the_text_helper():
    browser = Browser([page(1, kinds=("fill",)), page(2)])
    typed = []
    browser.act = lambda action, page, text=None: typed.append(text)
    _, result = run(browser, Provider("a0", "DONE"), fill_values={"g1": "42500"})
    assert result.goals[0].termination_reason == "provider_done" and typed == ["42500"]


def test_an_input_that_goes_stale_before_delivery_every_time_stops_after_three():
    """E2E-02 rerun: 'VPN Connection' went stale before input 120 times, nothing delivered."""
    browser = Browser([page(1)], errors=[StalePage("repainted")] * 10)
    provider = Provider(*["a0"] * 10)
    _, result = run(browser, provider)
    goal = result.goals[0]
    assert (goal.termination_reason, goal.mutation_state) == ("blocked", "none") and len(provider.requests) == 3


def test_an_unmet_toggle_is_reported_and_not_offered_again_until_the_page_state_changes():
    """E2E-02: VPN stayed checked (app rule); the fact goes back, and IPv4 turning on lifts the exclusion."""

    def toggles(n, vpn, ipv4):
        return {
            "fingerprint": f"p{n}", "semantic_fingerprint": f"vpn{vpn}-ip{ipv4}", "url": f"{ORIGIN}/a", "text": "",
            "actions": [
                {"id": "a0", "kind": "click", "label": "VPN Connection", "node": 1, "role": "checkbox", "checked": vpn},
                {"id": "a1", "kind": "click", "label": "Public IPv4", "node": 2, "role": "checkbox", "checked": ipv4},
            ],
        }  # fmt: skip

    browser = Browser([toggles(1, "true", "false"), toggles(2, "true", "false"), toggles(3, "true", "true"), toggles(4, "false", "true")])
    provider = Provider("a0", "a1", "a0", "DONE")
    _, result = run(browser, provider)
    goal = result.goals[0]
    assert [item.id for item in provider.requests[1].candidates] == ["a1"]
    assert [item.id for item in provider.requests[2].candidates] == ["a0", "a1"]
    assert goal.unmet_effects[0]["target"] == "VPN Connection" and goal.unmet_effects[0]["effect"] == "not_met"
    assert result.record()["goals"][0]["unmet_effects"][0]["after"] == "true"


def test_the_run_result_carries_the_page_it_ended_on():
    """E2E-I28: the outer agent observed after nearly every run to see where it ended."""
    browser = Browser([page(1), page(2)])

    def shared(request):
        loop = WindowsRunCoordinator(
            adapter_factory=lambda _r: loop.browser_adapter, decision_provider=Provider("a0", "DONE")
        )
        loop.browser_adapter = BrowserAdapter(browser, allowed_origins=request.allowed_origins)
        return loop

    result = RunCoordinator(shared_loop=shared).execute(RunRequest("f1", f"{ORIGIN}/a", (Goal("g1", "Save"),), (ORIGIN,)))
    assert result.final_state == {"url": f"{ORIGIN}/a", "title": "t", "text": ["x"]}
    assert result.record()["final_state"]["text"] == ["x"]


def test_a_met_toggle_that_fits_the_goal_is_verified_without_waiting_for_done():
    """E2E-I28: browser goals ended unverified; a met post-condition plus Jev fit now verifies them."""
    from finitact.achievement import EffectFitVerifier

    def toggle(n, checked):
        return {
            "fingerprint": f"p{n}", "semantic_fingerprint": f"s{checked}", "url": f"{ORIGIN}/a", "title": "t", "text": "Features",
            "actions": [{"id": "a0", "kind": "click", "label": "VPN", "node": 1, "role": "checkbox", "checked": checked}],
        }  # fmt: skip

    asked = []

    def fit(goal, view, action, _outcome):
        asked.append((goal, view, action))
        return True

    browser = Browser([toggle(1, "false"), toggle(2, "true")])
    provider = Provider("a0")
    _, result = run(browser, provider, goals=("Turn VPN on",), action_verifier=EffectFitVerifier(fit))
    goal = result.goals[0]
    assert (goal.termination_reason, goal.outcome) == ("outcome_verified", "verified_success")
    assert len(provider.requests) == 1
    _, view, action = asked[0]
    assert view["fields"] == ["[ ] VPN"] and (action["state_before"], action["state_after"]) == ("false", "true")


def until_page(n, text="x", unit="px"):
    return {
        "fingerprint": f"p{n}", "semantic_fingerprint": f"s{n}", "url": f"{ORIGIN}/a", "title": "t", "text": text,
        "actions": [{"id": "a0", "kind": "click", "label": "Next", "node": 0},
                    {"id": "a1", "kind": "select", "label": "Unit → px, mm", "current_value": unit, "node": 1}],
    }  # fmt: skip


def run_until(browser, provider, until, action_verifier=None):
    subject = coordinator(browser, provider, action_verifier)
    request = RunRequest("u1", f"{ORIGIN}/a", (Goal("g1", "Export in mm", until),), (ORIGIN,))
    return subject.execute(request).goals[0]


def test_an_until_already_shown_closes_the_goal_without_asking_the_provider():
    provider = Provider()
    goal = run_until(Browser([until_page(1, unit="mm")]), provider, Until(field="unit", value="MM"))
    assert (goal.termination_reason, goal.outcome) == ("outcome_verified", "verified_success")
    assert provider.requests == []


def test_a_done_before_the_until_is_rejected_into_history_and_the_goal_continues():
    browser = Browser([until_page(1), until_page(1), until_page(2, text="Export finished")])
    provider = Provider("DONE", "a0")
    goal = run_until(browser, provider, Until(text="export finished"))
    assert (goal.termination_reason, goal.outcome) == ("outcome_verified", "verified_success")
    assert browser.acted == ["a0"]
    assert provider.requests[1].history[-1]["action"] == "DONE rejected, not reached: text 'export finished' shown"


def test_dones_past_the_rejection_limit_end_unverified_with_what_is_missing():
    provider = Provider("DONE", "DONE", "DONE")
    goal = run_until(Browser([until_page(1)]), provider, Until(text="Export finished"))
    assert goal.termination_reason == "provider_done" and goal.outcome != "verified_success"
    assert goal.detail == "until not reached: text 'Export finished' shown"
    assert len(provider.requests) == 3


def test_one_action_achievement_does_not_close_an_until_goal():
    asked = []

    def verifier(*args, **kwargs):
        asked.append(args)
        return True

    browser = Browser([until_page(1), until_page(2), until_page(3, text="done")])
    goal = run_until(browser, Provider("a0", "a0"), Until(text="done"), action_verifier=verifier)
    assert goal.termination_reason == "outcome_verified" and browser.acted == ["a0", "a0"]
    assert asked == []


class DownloadingBrowser(Browser):
    def __init__(self, pages, finish_on_act):
        super().__init__(pages)
        self.download_state = {"old": {"state": "completed"}}
        self.finish_on_act, self.settles, self.finish_while_settling = finish_on_act, [], False

    def act(self, action, page, text=None):
        super().act(action, page, text)
        if len(self.acted) == self.finish_on_act:
            self.download_state["new"] = {"state": "completed"}
        elif len(self.acted) < self.finish_on_act:
            self.download_state["new"] = {"state": "inProgress"}

    def downloads(self, settle_s):
        self.settles.append(settle_s)
        if settle_s and self.finish_while_settling and self.download_state:
            self.download_state["new"] = {"state": "completed"}


def test_a_download_completed_before_the_goal_does_not_count():
    browser = DownloadingBrowser([until_page(1)], finish_on_act=2)
    goal = run_until(browser, Provider("a0", "a0"), Until(download=True))
    assert goal.termination_reason == "outcome_verified" and browser.acted == ["a0", "a0"]


def test_an_action_that_starts_a_download_waits_for_it_before_asking_again():
    """EXP-0011 trial 1: OK started the download, the next decision was unsure and the goal stopped unreached."""
    browser = DownloadingBrowser([until_page(1)], finish_on_act=9)
    browser.finish_while_settling = True
    provider = Provider("a0")
    goal = run_until(browser, provider, Until(download=True))
    assert goal.termination_reason == "outcome_verified" and len(provider.requests) == 1


def test_a_done_waits_for_a_download_still_being_written():
    browser = DownloadingBrowser([until_page(1)], finish_on_act=9)
    goal = run_until(browser, Provider("a0", "DONE", "DONE", "DONE"), Until(download=True))
    assert goal.detail == "until not reached: download completed"
    assert browser.settles.count(3.0) == 4


def test_until_on_a_browser_without_download_events_is_never_reached():
    browser = DownloadingBrowser([until_page(1)], finish_on_act=1)
    browser.download_state = None
    goal = run_until(browser, Provider("DONE", "DONE", "DONE"), Until(download=True))
    assert goal.detail == "until not reached: download completed (this browser reports no downloads)"
