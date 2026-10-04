"""Idempotent orchestration for ordered, policy-bounded Finitact runs."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Literal, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from .action_adapter import ActionAdapter, ActionOutcome, Freshness, MutationUncertain, Observation, ScopeViolation
from .achievement import launch_pending
from .agent import Agent
from .contracts import (
    EDGE_CONTOUR_SOURCE,
    FILL_PANEL_SOURCE,
    DecisionProvider,
    ObservedCandidate,
    TextHelper,
    windows_decision_request,
)
from .decision_cache import DecisionCache
from .model import ProviderBudgetExceeded
from .providers import OpenAITextHelper, TypeSafeDecisionProvider

# Goal results keep only an exception's type (journals omit page text); causes go to the local server log.
logger = logging.getLogger(__name__)

TerminationReason = Literal[
    "provider_done",
    "outcome_verified",
    "provider_blocked",
    "provider_uncertain",
    "blocked",
    "budget",
    "deadline",
    "cancelled",
    "error",
]
MutationState = Literal["none", "confirmed", "pending", "not_attempted", "uncertain", "mixed"]
OutcomeState = Literal["verified_success", "verified_failure", "unverified"]

# ADR-0019: below this the chooser is matching goal words it cannot find on screen, so the outer agent
# rewrites the goal from the returned labels instead of Finitact acting or giving up.
UNCERTAIN_CONFIDENCE = 0.4
# ADR-0020: a wrongly chosen BLOCKED reached 0.49 on solvable goals, while impossible ones scored 0.55 or more.
BLOCKED_CONFIDENCE = 0.6
UNCERTAIN_LABEL_COUNT = 5
UNCERTAIN_LABEL_CHARS = 120
# ADR-0041: an observe_window ref outlives the outer agent's next reply (1.3-4.3 s in E2E-03) but not a long pause.
OBSERVED_TTL_SECONDS = 60.0
# Same cap as the text helper output (model.field_text).
MAX_FILL_TEXT = 2000


@dataclass(frozen=True)
class Until:
    """EXP-0011: an observable end state that closes a goal, so its unseen middle steps run inside one goal.

    Only this closes such a goal: a provider DONE or one action's achievement is progress, not the goal's end.
    """

    download: bool = False
    field: str | None = None
    value: str | None = None
    text: str | None = None

    def __post_init__(self):
        if (self.field is None) != (self.value is None):
            raise ValueError("until field and value go together")
        if not (self.download or self.field or self.text):
            raise ValueError("until needs download, field/value or text")


@dataclass(frozen=True)
class AppExpect:
    """EXP-0015 (ADR-0017 D): a state value the target app itself publishes, compared after each action.

    The source is a JSON file an app-side plugin keeps writing with ``t`` (epoch seconds); only a sample
    written after the action and equal to ``equals`` verifies the goal. A stale, missing or different
    value gives no verdict, so the other verifiers still judge.
    """

    source: str
    key: str
    equals: str

    def __post_init__(self):
        if not (self.source.strip() and self.key.strip()):
            raise ValueError("app_expect needs source and key")


@dataclass(frozen=True)
class AppAnnotation:
    """EXP-0015 (ADR-0017 A): a fact the outer agent read from the app's own structure about one observed item.

    Attached only to the candidate whose normalized label equals ``match_text`` uniquely, on the observation
    the run starts from; it adds ``attributes["app"]`` and changes neither the label nor how it is acted on.
    """

    match_text: str
    role: str
    name: str
    value: str
    source: str

    def __post_init__(self):
        if not self.match_text.strip():
            raise ValueError("app annotation needs match text")


@dataclass(frozen=True)
class Goal:
    id: str
    goal: str
    until: Until | None = None
    app_expect: AppExpect | None = None

    def __post_init__(self):
        if not self.id.strip() or not self.goal.strip():
            raise ValueError("Goal id and goal must not be blank")


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    start_url: str
    goals: tuple[Goal, ...]
    allowed_origins: tuple[str, ...]
    denied_operations: tuple[str, ...] = ()
    deadline_ms: int = 60_000
    action_budget: int = 60
    provider_attempt_budget: int = 120
    # E2E-I15: continue in a tab a previous run kept (keep_tab), instead of a new tab at start_url.
    tab_id: str | None = None
    keep_tab: bool = False
    # Caller-given text per goal, as run_windows has (ADR-0026); the text helper spent 13.6 s in E2E-02 without it.
    fill_values: Mapping[str, str] = field(default_factory=dict)
    # ADR-0046: operations beyond BROWSER_OPERATIONS a run opts into; offering drag on every page would flood candidates.
    extra_operations: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.run_id.strip():
            raise ValueError("run_id must not be blank")
        if not self.goals or len({goal.id for goal in self.goals}) != len(self.goals):
            raise ValueError("goals must contain unique ids")
        if unknown := [op for op in self.extra_operations if op not in BROWSER_EXTRA_OPERATIONS]:
            raise ValueError(f"unknown extra operation: {unknown[0]}")
        if set(self.extra_operations) & set(self.denied_operations):
            raise ValueError("an operation cannot be both extra and denied")
        if not self.allowed_origins:
            raise ValueError("allowed_origins must not be empty")
        if min(self.deadline_ms, self.action_budget, self.provider_attempt_budget) <= 0:
            raise ValueError("deadline and budgets must be positive")

    def fingerprint(self) -> str:
        fields = _without_new_defaults(asdict(self))
        if not fields["extra_operations"]:
            del fields["extra_operations"]  # keeps fingerprints of runs recorded before ADR-0046
        body = json.dumps(fields, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class CandidatePick:
    """The first goal's ref: an item of the target's latest observation, acted on without the chooser (ADR-0043)."""

    ref: str

    def __post_init__(self):
        if not self.ref.strip():
            raise ValueError("pick ref must not be blank")


@dataclass(frozen=True)
class WindowsRunRequest:
    run_id: str
    target_id: str
    goals: tuple[Goal, ...]
    allowed_operations: tuple[str, ...]
    synthetic_input_allowed: bool = False
    exclusive_environment_ref: str | None = None
    decision_cache_allowed: bool = False
    deadline_ms: int = 60_000
    action_budget: int = 60
    provider_attempt_budget: int = 120
    click_label_constraints: Mapping[str, str] = field(default_factory=dict)
    # Caller-given fill text per goal; replaces the text helper's reading of the goal (ADR-0026).
    fill_values: Mapping[str, str] = field(default_factory=dict)
    # Goal id -> "find_and_click": search one list and click the constrained label without the provider (ADR-0027).
    selection_policies: Mapping[str, str] = field(default_factory=dict)
    pick: CandidatePick | None = None
    # ADR-0045: a second window that only drag ends may land in; never a target of any other operation.
    drop_target_id: str | None = None
    app_annotations: tuple[AppAnnotation, ...] = ()

    def __post_init__(self):
        if not self.run_id.strip() or not self.target_id.strip():
            raise ValueError("run_id and target_id must not be blank")
        if not self.goals or len({goal.id for goal in self.goals}) != len(self.goals):
            raise ValueError("goals must contain unique ids")
        if self.drop_target_id is not None:
            if "drag" not in self.allowed_operations:
                raise ValueError("a drop target needs drag in allowed_operations")
            if self.drop_target_id == self.target_id:
                raise ValueError("the drop target must differ from the target")
        if not self.allowed_operations:
            raise ValueError("allowed_operations must not be empty")
        if self.synthetic_input_allowed and not self.exclusive_environment_ref:
            raise ValueError("synthetic input requires an exclusive environment reference")
        if min(self.deadline_ms, self.action_budget, self.provider_attempt_budget) <= 0:
            raise ValueError("deadline and budgets must be positive")
        goal_ids = {goal.id for goal in self.goals}
        if any(goal_id not in goal_ids for goal_id in self.click_label_constraints):
            raise ValueError("click label constraints must reference a request goal")
        if any(not label.strip() for label in self.click_label_constraints.values()):
            raise ValueError("click label constraints must not be blank")
        if any(goal_id not in goal_ids for goal_id in self.fill_values):
            raise ValueError("fill values must reference a request goal")
        if any(not value.strip() or len(value) > MAX_FILL_TEXT for value in self.fill_values.values()):
            raise ValueError(f"fill values must be non-blank and at most {MAX_FILL_TEXT} characters")
        for goal_id, policy in self.selection_policies.items():
            if policy != "find_and_click":
                raise ValueError(f"unknown selection policy: {policy}")
            if goal_id not in self.click_label_constraints:
                raise ValueError("find_and_click needs a click label constraint for its goal")

    def fingerprint(self) -> str:
        fields = _without_new_defaults(asdict(self))
        if fields["drop_target_id"] is None:
            del fields["drop_target_id"]  # keeps fingerprints of runs recorded before ADR-0045
        body = json.dumps(
            {"backend": "windows", **fields}, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class GoalResult:
    goal_id: str
    termination_reason: TerminationReason
    mutation_state: MutationState
    outcome: OutcomeState
    detail: str | None
    journal_ref: str
    metrics: dict[str, int] = field(default_factory=dict)
    # Untrusted on-screen text with its pickable ref, set only for provider_uncertain.
    screen_candidates: tuple[dict, ...] = ()
    # Wall ms per stage for the ledger; the MCP output drops it so the caller's context is unchanged.
    stage_ms: dict[str, int] = field(default_factory=dict)
    # ADR-0039: delivered actions whose control did not reach the expected state (facts, no inferred rule).
    unmet_effects: tuple[dict, ...] = ()
    # Labels gone and new since the goal began, for a Windows goal that acted but could not be verified.
    screen_changes: dict | None = None

    @classmethod
    def from_record(cls, record: dict) -> "GoalResult":
        return cls(
            **{
                **record,
                "screen_candidates": tuple(record.get("screen_candidates", ())),
                "unmet_effects": tuple(record.get("unmet_effects", ())),
            }
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: Literal["completed", "partial", "stopped"]
    goals: tuple[GoalResult, ...]
    remaining_goal_ids: tuple[str, ...]
    journal_ref: str
    replayed: bool = False
    # The browser tab a later run may continue in (E2E-I15); absent for Windows runs.
    tab_id: str | None = None
    # The page the run ended on (browser_inventory.final_state), so a check needs no extra observe (E2E-I28).
    final_state: dict | None = None

    def record(self) -> dict:
        record = asdict(self)
        if record["tab_id"] is None:
            del record["tab_id"]
        if record["final_state"] is None:
            del record["final_state"]
        for goal in record["goals"]:
            if not goal["unmet_effects"]:
                del goal["unmet_effects"]
            if goal["screen_changes"] is None:
                del goal["screen_changes"]
        return record


class OutcomeVerifier(Protocol):
    def __call__(self, goal: Goal, snapshot: dict) -> bool | None: ...


def _continues_progress(decision, candidates, progressed) -> bool:
    if progressed is None or decision.terminal_reason or decision.choice is None:
        return False
    candidate = next((item for item in candidates if item.id == decision.choice), None)
    probabilities = decision.provider_metadata.get("probabilities") or {}
    direction, rect = progressed
    if candidate is None or candidate.operation != "scroll" or not rect or not probabilities:
        return False
    if probabilities.get(candidate.id) != max(probabilities.values()):
        return False
    # OCR jitters a list's rect between observations (two-list baseline), so the area is matched by containment.
    x, y, width, height = candidate.attributes.get("rect") or (0, 0, 0, 0)
    cx, cy = x + width / 2, y + height / 2
    return (
        candidate.attributes.get("direction") == direction
        and width > 0
        and rect[0] <= cx <= rect[0] + rect[2]
        and rect[1] <= cy <= rect[1] + rect[3]
    )


def _likely_candidates(decision, candidates, values=None) -> tuple[dict, ...]:
    # The outer agent picks or restates by these words, so shape-only labels would mislead it (BUG-0026).
    by_id = {
        candidate.id: candidate
        for candidate in candidates
        if candidate.attributes.get("source") != EDGE_CONTOUR_SOURCE
        or (candidate.operation == "drag" and candidate.attributes.get("drag_phase") == "end")
    }
    probabilities = decision.provider_metadata.get("probabilities") or {}
    ranked = sorted((key for key in probabilities if key in by_id), key=lambda key: -probabilities[key])
    likely: list[dict] = []
    for key in ranked:
        candidate = by_id[key]
        label = str(candidate.label)[:UNCERTAIN_LABEL_CHARS]
        kind = None if candidate.operation == "click" else candidate.operation
        # A panel label is a cut concatenation, so two panels can share it (ADR-0029); their rects differ.
        rect = candidate.attributes.get("rect") if candidate.attributes.get("source") == FILL_PANEL_SOURCE else None
        if not label or any(
            (item["label"], item.get("kind"), item.get("rect") if rect is not None else None)
            == (label, kind, list(rect) if rect is not None else None)
            for item in likely
        ):
            continue
        item = {"ref": candidate.id, "label": label}
        if kind is not None:
            # A fill shares its label and rect with the click on the same region.
            item["kind"] = kind
        rect = candidate.attributes.get("rect")
        if rect is not None:
            # Position and popup membership tell same-worded candidates apart (ADR-0019 a2 picked "Game").
            item["rect"] = list(rect)
            item["popup"] = candidate.attributes.get("scope") == "owned_popup"
        if (values or {}).get(key) is not None:
            # A captioned field is labelled by its caption alone (BUG-0034); its text still tells fields apart.
            item["current_value"] = values[key]
        likely.append(item)
        if len(likely) == UNCERTAIN_LABEL_COUNT:
            break
    return tuple(likely)


class _RefusedBeforeActing(Exception):
    pass


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Unsupported URL origin: {url}")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _add_usage(totals: dict[str, int], usage) -> None:
    # Provider usage shapes differ (TypeSafe, OpenAI-compatible, Ollama); only integer counters are summed.
    for key, value in (usage or {}).items():
        if isinstance(value, int) and not isinstance(value, bool):
            totals[key] = totals.get(key, 0) + value


class _StageTimer:
    # perf_counter, not the injectable run clock: timing must not consume or skew deadline ticks.
    def __init__(self, adapter) -> None:
        self.ms: dict[str, int] = {}
        self._adapter = adapter
        self._adapter_base = dict(getattr(adapter, "stage_ms", None) or {})

    @contextmanager
    def __call__(self, stage: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(stage, round((time.perf_counter() - started) * 1000))

    def add(self, stage: str, value: int) -> None:
        self.ms[stage] = self.ms.get(stage, 0) + value

    @contextmanager
    def observation(self, reason: str):
        # Reason spans are nested under observe. They stay in the ledger; the public result still
        # reports only the existing observe total, so diagnosis does not inflate the outer context.
        with self("observe"), self(f"observe_{reason}"):
            yield

    def decision(self, decision) -> None:
        heats = decision.provider_metadata.get("heats") or ()
        latencies = [heat.get("latency_ms", 0) for heat in heats]
        self.add("decide_requests", len(latencies) + 1)
        if latencies:
            self.add("decide_heats", sum(latencies))
            # Sum of per-decision maxima: the heat time left if the chunks ran concurrently.
            self.add("decide_heats_max", max(latencies))

    def result(self) -> dict[str, int]:
        adapter_ms = getattr(self._adapter, "stage_ms", None) or {}
        merged = dict(self.ms)
        for key, value in adapter_ms.items():
            delta = value - self._adapter_base.get(key, 0)
            if delta:
                merged[f"adapter_{key}"] = delta
        return merged


SCREEN_CHANGE_LABELS = 6


def _screen_changes(before: Observation, after: Observation) -> dict:
    """What an unverified goal changed on screen, so the caller need not observe to learn it acted.

    Tk drag in u1 (public-repo plan): the drag had landed, but a bare "unverified" sent the outer agent to observe,
    where a clipped label looked like no drop zone, and it reported BLOCKED.
    """

    def labels(observation: Observation) -> list[str]:
        seen: dict[str, None] = {}
        for candidate in observation.candidates:
            if candidate.attributes.get("source") == EDGE_CONTOUR_SOURCE:
                continue
            label = str(candidate.binding.get("original_label", candidate.label))[:UNCERTAIN_LABEL_CHARS]
            seen.setdefault(label, None)
        return list(seen)

    start, end = labels(before), labels(after)
    return {
        "gone": [label for label in start if label not in end][:SCREEN_CHANGE_LABELS],
        "new": [label for label in end if label not in start][:SCREEN_CHANGE_LABELS],
    }


def _mutation_state(attempts: list[dict]) -> MutationState:
    states = {attempt["status"] for attempt in attempts}
    if not states:
        return "none"
    if "uncertain" in states:
        return "uncertain"
    if len(states) == 1:
        return states.pop()
    return "mixed"


# A ledger row still "running" when another server process loads it has no live owner here (BUG-0028).
# It may have mutated before stopping, so it is never re-run, and pollers need a terminal status.
INTERRUPTED_DETAIL = (
    "run_id stopped before a safe result in an earlier or another server process; mutation state unknown. "
    "Observe the target and use a new run_id."
)


def _journal_view(run_id, entry):
    if entry.get("result"):
        status = "finished"
    elif entry.get("interrupted"):
        status = "interrupted"
    else:
        status = "running"
    events = list(entry["events"])
    if status == "interrupted":
        events.append({"goal_id": None, "termination_reason": "interrupted", "detail": INTERRUPTED_DETAIL})
    return {"run_id": run_id, "status": status, "events": events}


# Action kinds snapshot.js offers; denied_operations removes from these for the shared loop.
BROWSER_OPERATIONS = ("click", "fill", "select", "scroll", "wait")
BROWSER_EXTRA_OPERATIONS = ("drag",)


class RunCoordinator:
    """Own run identity, policy enforcement and result journaling outside providers."""

    def __init__(
        self,
        *,
        agent_factory=Agent,
        verifier: OutcomeVerifier | None = None,
        clock: Callable[[], float] = time.monotonic,
        ledger_path: Path | str | None = None,
        shared_loop: Callable[[RunRequest], "WindowsRunCoordinator"] | None = None,
    ):
        self.agent_factory = agent_factory
        # ADR-0037: when set, goals run through the shared loop with a BrowserAdapter instead of Agent.
        self.shared_loop = shared_loop
        self._shared_runs: dict[str, WindowsRunCoordinator] = {}
        self.verifier = verifier
        self.clock = clock
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        if self.ledger_path:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self._database() as database:
                database.execute(
                    "CREATE TABLE IF NOT EXISTS runs "
                    "(run_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT)"
                )

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                entry = self._load_entry(run_id)
            if entry is None or entry.get("result") is not None or entry.get("interrupted"):
                return False
            entry["cancelled"] = True
            shared = self._shared_runs.get(run_id)
        if shared is not None:
            shared.cancel(run_id)
        return True

    def journal(self, run_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                entry = self._load_entry(run_id)
            if entry is None:
                return None
            return _journal_view(run_id, entry)

    def execute(self, request: RunRequest) -> RunResult:
        fingerprint = request.fingerprint()
        with self._lock:
            prior = self._entries.get(request.run_id)
            if prior is None:
                prior = self._load_entry(request.run_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("run_id already belongs to a different request")
                if prior.get("result") is None:
                    raise RuntimeError(INTERRUPTED_DETAIL if prior.get("interrupted") else "run_id is already running")
                return replace(prior["result"], replayed=True)
            self._entries[request.run_id] = {
                "fingerprint": fingerprint,
                "cancelled": False,
                "events": [],
                "result": None,
            }
            self._save_entry(request.run_id, fingerprint, "running", None)

        try:
            result = self._execute_new(request)
        except Exception:
            with self._lock:
                self._entries.pop(request.run_id, None)
                self._delete_entry(request.run_id)
            raise
        with self._lock:
            self._entries[request.run_id]["result"] = result
            self._save_entry(request.run_id, fingerprint, "finished", result)
        return result

    def _execute_new(self, request: RunRequest) -> RunResult:
        allowed = {_origin(origin) for origin in request.allowed_origins}
        # A kept tab is not navigated, so start_url only matters for a new tab; the origin gate still runs each step.
        if request.tab_id is None and _origin(request.start_url) not in allowed:
            raise ValueError("start_url is outside allowed_origins")
        if self.shared_loop is not None:
            return self._execute_shared(request)
        if any(goal.until for goal in request.goals):
            raise ValueError("until needs the shared browser loop")
        if request.extra_operations:
            raise ValueError("extra_operations needs the shared browser loop")
        started = self.clock()
        results: list[GoalResult] = []
        agent = None
        try:
            first = request.goals[0]
            agent = self.agent_factory(
                request.start_url,
                first.goal,
                action_budget=request.action_budget,
                provider_attempt_budget=request.provider_attempt_budget,
            )
            for index, goal in enumerate(request.goals):
                if index:
                    agent.continue_with(goal.goal)
                result = self._execute_goal(request, goal, agent, allowed, started)
                results.append(result)
                if result.termination_reason != "provider_done":
                    break
        except Exception as exc:
            goal = request.goals[len(results)]
            results.append(
                GoalResult(
                    goal.id,
                    "error",
                    _mutation_state(agent.state["mutation_attempts"]) if agent else "none",
                    "unverified",
                    type(exc).__name__,
                    f"finitact://runs/{request.run_id}/goals/{goal.id}",
                )
            )
            self._event(request.run_id, "error", goal.id, type(exc).__name__)
        finally:
            if agent is not None:
                agent.close()

        remaining = tuple(goal.id for goal in request.goals[len(results) :])
        status = "completed" if not remaining and all(r.termination_reason == "provider_done" for r in results) else (
            "partial" if results and any(r.termination_reason == "provider_done" for r in results) else "stopped"
        )
        return RunResult(
            request.run_id,
            status,
            tuple(results),
            remaining,
            f"finitact://runs/{request.run_id}",
        )

    def _execute_shared(self, request: RunRequest) -> RunResult:
        loop = self.shared_loop(request)
        with self._lock:
            self._shared_runs[request.run_id] = loop
        try:
            result = loop.execute(
                WindowsRunRequest(
                    run_id=request.run_id,
                    target_id=f"browser:{request.start_url}",
                    goals=request.goals,
                    allowed_operations=(
                        *(op for op in BROWSER_OPERATIONS if op not in request.denied_operations),
                        *request.extra_operations,
                    ),
                    deadline_ms=request.deadline_ms,
                    action_budget=request.action_budget,
                    provider_attempt_budget=request.provider_attempt_budget,
                    fill_values=dict(request.fill_values),
                )
            )
            tab = getattr(loop, "browser_tab", None)
            if tab and (request.keep_tab or request.tab_id):
                result = replace(result, tab_id=tab)
            adapter = getattr(loop, "browser_adapter", None)
            page = getattr(adapter, "page", None)
            if page is not None:
                from .browser_inventory import final_state

                downloads = getattr(getattr(adapter, "browser", None), "downloads", None)
                result = replace(result, final_state=final_state(page, downloads() if callable(downloads) else ()))
            return result
        finally:
            with self._lock:
                self._shared_runs.pop(request.run_id, None)

    def _execute_goal(self, request, goal, agent, allowed, started) -> GoalResult:
        mutation_start = len(agent.state["mutation_attempts"])
        reason: TerminationReason
        detail = None
        while True:
            if self._cancelled(request.run_id):
                reason, detail = "cancelled", "run cancellation requested"
                break
            if (self.clock() - started) * 1000 >= request.deadline_ms:
                reason, detail = "deadline", "run deadline reached"
                break
            if _origin(agent.state["page"]["url"]) not in allowed:
                reason, detail = "blocked", "page origin is outside allowed_origins"
                break
            try:
                snapshot = agent.command("predict", {})
                selected = snapshot["decision"]["choice"]
                if self._cancelled(request.run_id):
                    reason, detail = "cancelled", "run cancellation requested"
                    break
                if (self.clock() - started) * 1000 >= request.deadline_ms:
                    reason, detail = "deadline", "run deadline reached"
                    break
                if selected not in {"DONE", "BLOCKED"}:
                    action = next(item for item in agent.state["page"]["actions"] if item["id"] == selected)
                    if action["kind"] in request.denied_operations:
                        reason, detail = "blocked", f"operation denied by policy: {action['kind']}"
                        break
                agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
            except Exception as exc:
                attempts = agent.state["mutation_attempts"][mutation_start:]
                if attempts and attempts[-1]["status"] == "uncertain":
                    reason, detail = "error", f"uncertain mutation: {type(exc).__name__}"
                elif "budget" in str(exc).lower():
                    reason, detail = "budget", str(exc)
                else:
                    reason, detail = "error", type(exc).__name__
                break
            if agent.state["status"] == "done":
                reason = "provider_done"
                break
            if agent.state["status"] == "blocked":
                reason = "provider_blocked"
                break

        attempts = agent.state["mutation_attempts"][mutation_start:]
        outcome: OutcomeState = "unverified"
        if self.verifier is not None:
            verified = self.verifier(goal, agent.snapshot())
            outcome = "verified_success" if verified is True else "verified_failure" if verified is False else outcome
        self._event(request.run_id, reason, goal.id, detail)
        return GoalResult(
            goal.id,
            reason,
            _mutation_state(attempts),
            outcome,
            detail,
            f"finitact://runs/{request.run_id}/goals/{goal.id}",
        )

    def _cancelled(self, run_id):
        with self._lock:
            return self._entries[run_id]["cancelled"]

    def _event(self, run_id, reason, goal_id, detail):
        with self._lock:
            self._entries[run_id]["events"].append(
                {"goal_id": goal_id, "termination_reason": reason, "detail": detail}
            )

    def _database(self):
        return sqlite3.connect(self.ledger_path)

    def _save_entry(self, run_id, fingerprint, status, result):
        if not self.ledger_path:
            return
        payload = json.dumps(result.record()) if result else None
        with self._database() as database:
            database.execute(
                "INSERT OR REPLACE INTO runs(run_id, fingerprint, status, result) VALUES (?, ?, ?, ?)",
                (run_id, fingerprint, status, payload),
            )

    def _load_entry(self, run_id):
        if not self.ledger_path:
            return None
        with self._database() as database:
            row = database.execute(
                "SELECT fingerprint, status, result FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        fingerprint, status, payload = row
        result = None
        if status == "finished" and payload:
            record = json.loads(payload)
            result = RunResult(
                run_id=record["run_id"],
                status=record["status"],
                goals=tuple(GoalResult.from_record(goal) for goal in record["goals"]),
                remaining_goal_ids=tuple(record["remaining_goal_ids"]),
                journal_ref=record["journal_ref"],
                replayed=record.get("replayed", False),
                tab_id=record.get("tab_id"),
                final_state=record.get("final_state"),
            )
        events = [] if result is None else [
            {
                "goal_id": goal.goal_id,
                "termination_reason": goal.termination_reason,
                "detail": goal.detail,
            }
            for goal in result.goals
        ]
        entry = {
            "fingerprint": fingerprint,
            "cancelled": False,
            "events": events,
            "result": result,
            "interrupted": result is None,
        }
        self._entries[run_id] = entry
        return entry

    def _delete_entry(self, run_id):
        if self.ledger_path:
            with self._database() as database:
                database.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


STALE_DETAIL = "the same decision became stale 3 times; the page keeps changing"


def _error_detail(exc: BaseException) -> str:
    """Type and a short reason (E2E-I27: a bare ValueError told the outer agent nothing); Finitact's own messages."""

    reason = " ".join(str(exc).split())[:160]
    return f"{type(exc).__name__}: {reason}" if reason else type(exc).__name__


def _shows_text(label: str | None, text: str) -> bool:
    # OCR spaces out glyphs, drops a closing quote and merges the tab name and line numbers into the editor's label;
    # a short text must match whole, since a few characters recur by chance.
    shown, typed = re.sub(r"\s+", "", label or "").lower(), re.sub(r"\s+", "", text).lower()
    if len(typed) < 8:
        return shown == typed
    run = difflib.SequenceMatcher(None, shown, typed, autojunk=False).find_longest_match().size
    return run >= 0.8 * len(typed)


def _stale_again(streak: list[str], choice: str) -> bool:
    """Record one more stale decision; True once the same choice went stale three times in a row."""

    if streak and streak[-1] != choice:
        streak.clear()
    streak.append(choice)
    return len(streak) >= 3


# ADR-0035/0036: E judges these from their own rect; the others need screen evidence only for a send.
ARRIVAL_OPERATIONS = ("click", "fill", "scroll")
COMMIT_OPERATIONS = ("key", "click", "double_click")
# A cold VSCode Quick Open draws the typed value after the first capture, which left E without a landing (E2E-I47).
SETTLE_OPERATIONS = (*COMMIT_OPERATIONS, "fill")
COMMIT_SETTLE_OBSERVATIONS = 2
COMMIT_SETTLE_SECONDS = 0.3
LAUNCH_POLLS = 12
LAUNCH_POLL_SECONDS = 0.25
# A DONE before the until state is re-asked this many times; a download still being written after an action or DONE gets this long.
UNTIL_DONE_REJECTIONS = 2
UNTIL_SETTLE_SECONDS = 3.0
# EXP-0015: an app applies a click on its next frame; a feed sample this long after the action reflects it.
APP_EXPECT_SETTLE_SECONDS = 0.3
APP_EXPECT_WAIT_SECONDS = 2.0


class WindowsRunCoordinator:
    """Execute bounded finite-choice runs through one injected Windows ActionAdapter."""

    def __init__(
        self,
        *,
        adapter_factory: Callable[[WindowsRunRequest], ActionAdapter],
        decision_provider: DecisionProvider | None = None,
        decision_cache: DecisionCache | None = None,
        text_helper: TextHelper | None = None,
        verifier: Callable[[Goal, Observation], bool | None] | None = None,
        action_verifier: Callable[[ActionOutcome], bool | None] | None = None,
        window_probe: Callable[[], Sequence[Mapping]] | None = None,
        target_guard: Callable[[str], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        ledger_path: Path | str | None = None,
        scroll_progress_gate: bool = False,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.adapter_factory = adapter_factory
        # An app feed stamps samples in epoch seconds; the monotonic clock cannot be compared with them.
        self.wall_clock = wall_clock
        # EXP-0013: off by default until the live A/D comparison decides it.
        self.scroll_progress_gate = scroll_progress_gate
        # BUG-0041: names why a target must not be acted on at all (the caller's own terminal), else None.
        self.target_guard = target_guard
        # Judges the last action from its before/after screens (ADR-0030 追記1); consulted only when
        # ``verifier`` has not already said failure, so an app-specific failure oracle outranks it.
        self.action_verifier = action_verifier
        # E2E-I3: a launched app's window is outside the target's frames; the verifier compares window lists.
        self.window_probe = window_probe
        self.decision_provider = decision_provider or TypeSafeDecisionProvider()
        self.decision_cache = decision_cache
        self.text_helper = text_helper or OpenAITextHelper()
        self.verifier = verifier
        self.clock = clock
        self._sleep = time.sleep
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        # The latest observation per target that a goal ref can name: an observe_window or the observation an
        # uncertain run ended on (ADR-0043). Process-local only: after a restart a ref finds nothing and fails closed.
        self._observed: dict[str, dict] = {}
        if self.ledger_path:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.ledger_path) as database:
                database.execute(
                    "CREATE TABLE IF NOT EXISTS runs "
                    "(run_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT)"
                )

    def keep_observation(self, target_id: str, observation, frames, item_refs: Mapping[str, Mapping[str, str]]) -> None:
        """Make an observation the target's latest, so the next run's first goal ref can name one of its items."""

        entry = {
            "observation": observation,
            "frames": frames,
            "item_refs": {ref: dict(ids) for ref, ids in item_refs.items()},
            "kept_at": self.clock(),
            "target_id": target_id,
        }
        with self._lock:
            self._observed[_window_key(target_id)] = entry

    def execute(self, request: WindowsRunRequest) -> RunResult:
        fingerprint = request.fingerprint()
        with self._lock:
            prior = self._entries.get(request.run_id)
            if prior is None:
                prior = self._load_entry(request.run_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("run_id already belongs to a different request")
                if prior["result"] is None:
                    raise RuntimeError(INTERRUPTED_DETAIL if prior.get("interrupted") else "run_id is already running")
                return replace(prior["result"], replayed=True)
            self._entries[request.run_id] = {
                "fingerprint": fingerprint,
                "result": None,
                "cancelled": False,
                "events": [],
            }
            self._save_entry(request.run_id, fingerprint, "running", None)

        result = self._execute_new(request)
        with self._lock:
            self._entries[request.run_id]["result"] = result
            self._save_entry(request.run_id, fingerprint, "finished", result)
        return result

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                entry = self._load_entry(run_id)
            if entry is None or entry["result"] is not None or entry.get("interrupted"):
                return False
            entry["cancelled"] = True
            return True

    def journal(self, run_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is None:
                entry = self._load_entry(run_id)
            if entry is None:
                return None
            return _journal_view(run_id, entry)

    def _execute_new(self, request: WindowsRunRequest) -> RunResult:
        started = self.clock()
        results: list[GoalResult] = []
        adapter = None
        observation = None
        close_error = None
        try:
            refusal = self.target_guard(request.target_id) if self.target_guard else None
            drop_id = getattr(request, "drop_target_id", None)
            if refusal is None and drop_id and self.target_guard:
                refusal = self.target_guard(drop_id)  # ADR-0045: a protected window is no drop target either
            if refusal:
                raise _RefusedBeforeActing(refusal)
            retained = self._take_retained(request)
            adapter = self.adapter_factory(request)
            timer = _StageTimer(adapter)
            picked = None
            if retained is not None:
                observation, picked = self._adopt(request, adapter, retained)
            else:
                with timer.observation("initial"):
                    observation = adapter.observe()
            run_start = observation
            # ADR-0037: the browser keeps history and provider attempts across goals (Agent.continue_with).
            run_scope = ([], []) if getattr(adapter, "budget_scope", "goal") == "run" else None
            for goal in request.goals:
                result, observation = self._execute_goal(
                    request, goal, adapter, observation, started, timer, picked, run_scope, run_start
                )
                picked = None
                timer = _StageTimer(adapter)
                results.append(result)
                if result.termination_reason not in {"provider_done", "outcome_verified"}:
                    break
        except _RefusedBeforeActing as exc:
            goal = request.goals[0]
            results.append(self._goal_result(request, goal, "blocked", "none", "unverified", str(exc)))
            self._event(request.run_id, "blocked", goal.id, str(exc))
        except Exception as exc:
            logger.exception("run %s stopped with an error", request.run_id)
            if len(results) < len(request.goals):
                goal = request.goals[len(results)]
                results.append(self._goal_result(request, goal, "error", "none", "unverified", _error_detail(exc)))
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except Exception as exc:
                    close_error = type(exc).__name__

        if close_error:
            self._event(request.run_id, "error", None, f"adapter close failed: {close_error}")
        remaining = tuple(goal.id for goal in request.goals[len(results) :])
        successful_reasons = {"provider_done", "outcome_verified"}
        if not remaining and all(result.termination_reason in successful_reasons for result in results):
            status = "completed"
        else:
            status = "partial" if any(
                result.termination_reason in successful_reasons for result in results
            ) else "stopped"
        return RunResult(request.run_id, status, tuple(results), remaining, f"finitact://runs/{request.run_id}")

    def _take_retained(self, request):
        with self._lock:
            # Any run on the window may change it, so its refs end here whether picked or not; one-shot either way.
            observed = self._observed.pop(_window_key(request.target_id), None)
        if request.pick is None:
            return None
        if observed is None:
            raise _RefusedBeforeActing("pick: no observation of this window; observe the window again")
        if observed["target_id"] != request.target_id:
            # BUG-0049: a screen item is a point for SendInput and a UIA item an element; neither acts as the other.
            raise _RefusedBeforeActing(
                "pick: observe_window refs act through synthetic input; call without synthetic_input_allowed false"
                if observed["target_id"].startswith("screen:")
                else "pick: this ref acts only with synthetic_input_allowed false"
            )
        if self.clock() - observed["kept_at"] > OBSERVED_TTL_SECONDS:
            raise _RefusedBeforeActing("pick: the observation expired; observe the window again")
        operations = observed["item_refs"].get(request.pick.ref)
        if operations is None:
            raise _RefusedBeforeActing("pick: ref was not offered by the observation")
        # An observe item offers its click and fill; the goal's fill value says which is meant. A run's candidate
        # is one action already, so it is taken as offered (the loop fills after a picked click, ADR-0022).
        wanted = "fill" if request.fill_values.get(request.goals[0].id) is not None else "click"
        operation = wanted if wanted in operations else next(iter(operations)) if len(operations) == 1 else None
        if operation is None:
            raise _RefusedBeforeActing(f"pick: the item offers no {wanted}")
        if operation not in request.allowed_operations:
            raise _RefusedBeforeActing(f"pick: {operation} is not an allowed operation")
        return {**observed, "candidate_id": operations[operation]}

    def _adopt(self, request, adapter, retained):
        adopt = getattr(adapter, "adopt", None)
        if adopt is None:
            raise _RefusedBeforeActing("pick: adapter cannot adopt a prior observation")
        observation = retained["observation"]
        adopt(observation, retained["frames"])
        candidate = next(item for item in observation.candidates if item.id == retained["candidate_id"])
        return observation, candidate

    def _retain(self, request, adapter, observation, candidates):
        retain = getattr(adapter, "retain", None)
        if retain is None or not candidates:
            return
        by_id = {item.id: item for item in observation.candidates}
        refs = {item["ref"]: {by_id[item["ref"]].operation: item["ref"]} for item in candidates}
        self.keep_observation(request.target_id, observation, retain(observation), refs)

    def _execute_goal(
        self, request, goal, adapter, observation, started, timer, picked=None, run_scope=None, run_start=None
    ):
        goal_start = observation
        annotations = getattr(request, "app_annotations", ())
        history: list[dict] = run_scope[0] if run_scope else []
        ineffective: list[tuple[Observation, str]] = []
        untypable: set[tuple[str, str]] = set()
        # EXP-0013: (direction, rect) of the last input when it was a scroll that brought new rows into its own area.
        progressed: tuple[str, Sequence[int]] | None = None
        progress_gates = 0
        # E2E-I16: (state, action) pairs whose action led back to a state already seen in this goal, such as a
        # dropdown opened and closed again; each round trip changes the screen, so the no-change stop misses it.
        seen_states: set[str] = {observation.semantic_id}
        cycling: set[tuple[str, str]] = set()
        # The same decision going stale again and again (a page that keeps repainting) re-asked Jev 120 times in
        # E2E-02; Agent.tick stopped at 3 and the shared loop lost that stop (ADR-0037).
        stale_streak: list[str] = []
        unchanged = getattr(adapter, "unchanged", None) or (lambda _before, _after: False)
        provider_attempts: list[dict] = run_scope[1] if run_scope else []
        # ADR-0037 seams: path-specific request shape, scope gate and non-input choices (the browser's wait).
        build_request = getattr(adapter, "decision_request", None)
        check_scope = getattr(adapter, "check_scope", None)
        non_mutating = getattr(adapter, "non_mutating", frozenset())
        text_context = getattr(adapter, "text_context", None)
        effect_of = getattr(adapter, "effect", None)
        fit_view = getattr(adapter, "fit_view", None)
        unmet: set[tuple[str, str, str | None]] = set()
        unmet_effects: list[dict] = []
        provider_tokens: dict[str, int] = {}
        mutations: list[dict] = []
        decision_cache_hits = 0
        direct_picks = 0
        policy_actions = 0
        detail = None
        reason: TerminationReason = "error"
        last_verification: bool | None = None
        screen_candidates: tuple[dict, ...] = ()
        search = _Search() if request.selection_policies.get(goal.id) == "find_and_click" else None
        if not observation.complete:
            detail = "incomplete observation"
            result = self._goal_result(request, goal, "error", "none", "unverified", detail)
            self._event(request.run_id, "error", goal.id, detail)
            return result, observation
        if self.verifier is not None:
            try:
                # A goal's success is judged against the state before that goal, not before the run:
                # goal 2 closing what goal 1 opened is invisible against a run-start baseline.
                begin_goal = getattr(self.verifier, "begin_goal", None)
                if begin_goal is not None:
                    begin_goal(goal)
                with timer("verify"):
                    last_verification = self.verifier(goal, observation)
            except Exception as exc:
                detail = f"outcome verification failed: {type(exc).__name__}"
                result = self._goal_result(request, goal, "error", "none", "unverified", detail)
                self._event(request.run_id, "error", goal.id, detail)
                return result, observation
            if last_verification is True:
                result = self._goal_result(
                    request, goal, "outcome_verified", "none", "verified_success", stage_ms=timer.result()
                )
                self._event(request.run_id, "outcome_verified", goal.id, None)
                return result, observation
        reach = getattr(adapter, "until_unmet", None) if goal.until is not None else None
        if goal.until is not None and reach is None:
            detail = "this target cannot check until"
            result = self._goal_result(request, goal, "error", "none", "unverified", detail)
            self._event(request.run_id, "error", goal.id, detail)
            return result, observation
        until_baseline = adapter.until_baseline() if reach is not None else None
        rejected_done = 0
        if reach is not None:
            with timer("verify"):
                reached = not reach(goal.until, observation, until_baseline)
            if reached:
                result = self._goal_result(
                    request, goal, "outcome_verified", "none", "verified_success", stage_ms=timer.result()
                )
                self._event(request.run_id, "outcome_verified", goal.id, None)
                return result, observation
        while True:
            gated = False
            if self._cancelled(request.run_id):
                reason, detail = "cancelled", "run cancellation requested"
                break
            if (self.clock() - started) * 1000 >= request.deadline_ms:
                reason, detail = "deadline", "run deadline reached"
                break
            if not observation.complete:
                reason, detail = "error", "incomplete observation"
                break
            if check_scope is not None:
                try:
                    check_scope(observation)
                except ScopeViolation as exc:
                    reason, detail = "blocked", str(exc)
                    break
            if len(provider_attempts) >= request.provider_attempt_budget:
                reason, detail = "budget", "Reached the run's provider-attempt budget"
                break

            allowed_candidates = _goal_candidates(request, goal, observation.candidates)
            # The outer agent read the app against the screen it saw; a later screen may hold other controls.
            if annotations and observation is run_start:
                allowed_candidates = _annotated(allowed_candidates, annotations)
            # E2E-I8: an action that left the screen unchanged is not offered again on that same screen;
            # Jev re-chose an Enter with no visible effect 21 times. Any later change lifts the exclusion.
            ineffective = [(seen, choice) for seen, choice in ineffective if unchanged(seen, observation)]
            spent = {choice for _seen, choice in ineffective}
            # A not_met action is withheld only on the same observed state with the same text (ADR-0039).
            spent |= {
                choice
                for state, choice, typed in unmet
                if state == observation.semantic_id and typed in (None, request.fill_values.get(goal.id))
            }
            spent |= {choice for state, choice in cycling if state == observation.semantic_id}
            spent |= {choice for state, choice in untypable if state == observation.semantic_id}
            # BUG-0061: re-filling a field that already shows this goal's delivered text changes nothing, yet Jev
            # chose it over Ctrl+S in 2/10 VSCode trials and spent the two-action budget.
            typed = {item["text"] for item in history if item["kind"] == "fill" and item.get("text")}
            spent |= {
                item.id
                for item in allowed_candidates
                if item.operation == "fill"
                and any(
                    _shows_text(item.label, text) or _shows_text(observation.untrusted_values.get(item.id), text)
                    for text in typed
                )
            }
            if spent:
                allowed_candidates = [item for item in allowed_candidates if item.id not in spent]
                if not allowed_candidates and picked is None:
                    reason, detail = "blocked", "no offered action changed the screen"
                    break
            forced = picked is not None
            planned = None
            if search is not None and not forced:
                planned = search.next(allowed_candidates, history)
                if planned == "exhausted":
                    reason, detail = "blocked", "find_and_click: label not found after scrolling the list both ways"
                    break
            if forced:
                candidate, picked = picked, None
                direct_picks += 1
            elif planned is not None:
                candidate = planned
                policy_actions += 1
            else:
                allowed_ids = {item.id for item in allowed_candidates}
                decision_observation = replace(
                    observation,
                    candidates=allowed_candidates,
                    untrusted_values={
                        candidate_id: value
                        for candidate_id, value in observation.untrusted_values.items()
                        if candidate_id in allowed_ids
                    },
                )
                decision_request = (build_request or windows_decision_request)(
                    decision_observation,
                    goal.goal,
                    history,
                    request.provider_attempt_budget - len(provider_attempts),
                    _goal_inputs(request, goal),
                )
                use_decision_cache = self.decision_cache is not None and request.decision_cache_allowed
                decision = self.decision_cache.get(decision_request) if use_decision_cache else None
                if decision is not None:
                    decision_cache_hits += 1
                if decision is None:
                    try:
                        with timer("decide"):
                            decision = self.decision_provider.decide(
                                decision_request,
                                attempts=provider_attempts,
                                call_id=f"decision-{len(history) + 1}",
                            )
                    except Exception as exc:  # noqa: BLE001 - BUG-0075: an earlier input must stay in the result
                        logger.exception("run %s: provider failed", request.run_id)
                        reason = "budget" if isinstance(exc, ProviderBudgetExceeded) else "error"
                        detail = _error_detail(exc)
                        break
                    timer.decision(decision)
                    _add_usage(provider_tokens, decision.measurements.get("usage"))
                    if use_decision_cache:
                        assert self.decision_cache is not None
                        self.decision_cache.put(decision_request, decision)
                # The provider can take seconds; a cancel or the deadline in that time still stops the input.
                if self._cancelled(request.run_id):
                    reason, detail = "cancelled", "run cancellation requested"
                    break
                if (self.clock() - started) * 1000 >= request.deadline_ms:
                    reason, detail = "deadline", "run deadline reached"
                    break
                if search is not None and search.clicked and decision.terminal_reason != "done":
                    # One click is the policy's whole selection; a second is the provider's call to repeat it.
                    reason, detail = "blocked", "find_and_click: the provider did not confirm done after the click"
                    break
                confidence = decision.provider_metadata.get("confidence")
                threshold = BLOCKED_CONFIDENCE if decision.terminal_reason == "blocked" else UNCERTAIN_CONFIDENCE
                uncertain = (
                    isinstance(confidence, (int, float))
                    and decision.terminal_reason != "done"
                    and confidence < threshold
                )
                # EXP-0013: a whole-screen change is no progress proof (a clock at a list's end), so only a scroll
                # whose previous same-way step revealed rows inside the same area skips the confidence gate.
                gated = uncertain and _continues_progress(decision, allowed_candidates, progressed)
                if gated:
                    uncertain = False
                if decision.terminal_reason or uncertain:
                    with timer("fresh"):
                        freshness = adapter.fresh(observation)
                    if freshness is not Freshness.FRESH:
                        if _stale_again(stale_streak, decision.choice or decision.terminal_reason):
                            reason, detail = "blocked", STALE_DETAIL
                            break
                        with timer.observation("stale_terminal"):
                            observation = adapter.observe()
                        continue
                    if uncertain:
                        reason = "provider_uncertain"
                        detail = f"decision confidence {confidence:.2f} below {threshold}"
                        labels = {item.id: f"{item.operation}:{item.label}" for item in observation.candidates}
                        logger.info(
                            "run %s uncertain: %s",
                            request.run_id,
                            {
                                labels.get(key, key): round(value, 2)
                                for key, value in (decision.provider_metadata.get("probabilities") or {}).items()
                            },
                        )
                        screen_candidates = _likely_candidates(
                            decision, allowed_candidates, observation.untrusted_values
                        )
                        self._retain(request, adapter, observation, screen_candidates)
                        break
                    if decision.terminal_reason == "done" and reach is not None:
                        with timer("verify"):
                            missing = reach(goal.until, observation, until_baseline, UNTIL_SETTLE_SECONDS)
                        if not missing:
                            reason, last_verification = "outcome_verified", True
                            break
                        if rejected_done < UNTIL_DONE_REJECTIONS:
                            rejected_done += 1
                            history.append(
                                {"action": "DONE rejected, not reached: " + "; ".join(missing), "kind": "done",
                                 "choice": "DONE", "text": None, "semantic_changed": None}
                            )  # fmt: skip
                            with timer.observation("until_rejected"):
                                observation = adapter.observe()
                            continue
                        reason, last_verification = "provider_done", False
                        detail = "until not reached: " + "; ".join(missing)
                        break
                    reason = "provider_done" if decision.terminal_reason == "done" else "provider_blocked"
                    break

                candidate = next((item for item in allowed_candidates if item.id == decision.choice), None)
                if candidate is None:
                    reason, detail = "blocked", "provider selected a candidate denied by policy"
                    break
            if candidate not in allowed_candidates:
                reason, detail = "blocked", "picked candidate denied by click label constraint"
                break
            if candidate.operation not in request.allowed_operations:
                reason, detail = "blocked", f"operation denied by policy: {candidate.operation}"
                break
            if candidate.operation == "drag" and candidate.attributes.get("drag_phase") == "start":
                continue_drag = getattr(adapter, "drag_end_observation", None)
                if continue_drag is None:
                    reason, detail = "blocked", "adapter cannot continue drag selection"
                    break
                try:
                    with timer.observation("drag_end"):
                        observation = continue_drag(candidate, observation)
                except Exception as exc:
                    reason, detail = "error", f"drag selection failed: {type(exc).__name__}"
                    break
                if not observation.complete or not observation.candidates:
                    reason, detail = "blocked", "drag has no observed end candidates"
                    break
                continue
            # The browser's budget counts every chosen action of the run, waits included (agent.py).
            if (len(history) if run_scope else len(mutations)) >= request.action_budget:
                reason, detail = "budget", "Reached the run's action budget"
                break

            takes_text = candidate.operation in {"fill", "set_range"}
            text = request.fill_values.get(goal.id) if takes_text else None
            if takes_text and text is None:
                try:
                    with timer("text"):
                        text, text_metadata = self.text_helper.generate(
                            text_context(observation, candidate, goal.goal, history)
                            if text_context is not None
                            else {
                                "goal": goal.goal,
                                "field": candidate.provider_record(),
                                "current_value": observation.untrusted_values.get(candidate.id),
                                "recent_actions": history[-6:],
                            },
                            attempts=provider_attempts,
                            call_id=f"text-{len(history) + 1}",
                            attempt_limit=request.provider_attempt_budget,
                        )
                except ValueError:
                    # E2E-I36: "click the Email field" chose fill, and the goal names nothing to type. Nothing
                    # was input; that fill is withheld on this screen and the provider chooses again.
                    untypable.add((observation.semantic_id, candidate.id))
                    history.append(
                        {"action": candidate.label, "kind": "fill", "choice": candidate.id, "text": None,
                         "result": "not typed: the goal gives no text for this field"}
                    )  # fmt: skip
                    continue
                _add_usage(provider_tokens, text_metadata.get("usage") if isinstance(text_metadata, dict) else None)
            with timer("fresh"):
                freshness = adapter.fresh(observation, candidate)
            if freshness is not Freshness.FRESH and forced:
                reason, detail = "blocked", "pick: the picked candidate's window changed; not acted"
                break
            if freshness is not Freshness.FRESH:
                if _stale_again(stale_streak, candidate.id):
                    reason, detail = "blocked", STALE_DETAIL
                    break
                with timer.observation("stale_candidate"):
                    observation = adapter.observe()
                continue

            attempt = {"status": "attempted", "choice": candidate.id, "kind": candidate.operation}
            if gated:
                attempt["gate"] = "scroll_progress"
                progress_gates += 1
                logger.info("run %s passes scroll %r on earlier progress", request.run_id, candidate.label)
            if candidate.operation not in non_mutating:
                mutations.append(attempt)
            logger.info("run %s acts %s on %r", request.run_id, candidate.operation, candidate.label)
            windows_before = self._probe_windows()
            acted_at = self.wall_clock()
            try:
                with timer("act"):
                    mutation = adapter.act(candidate, observation, text=text)
            except (MutationUncertain, ScopeViolation) as exc:
                attempt.update(status="uncertain", result=type(exc).__name__)
                reason, detail = "error", f"uncertain mutation: {type(exc).__name__}" + (f": {exc}" if str(exc) else "")
                break
            except Exception as exc:
                attempt.update(status="uncertain", result=type(exc).__name__)
                reason, detail = "error", f"uncertain mutation: {_error_detail(exc)}"
                break
            if mutation.status == "not_attempted" and str(mutation.detail).startswith("stale_before_input"):
                # Checked before any input (the browser's StalePage): the decision is spent, choose again.
                if attempt in mutations:
                    mutations.remove(attempt)
                # E2E-02 rerun: a toggle on a repainting page went stale here 120 times with nothing delivered.
                if _stale_again(stale_streak, candidate.id):
                    reason, detail = "blocked", STALE_DETAIL
                    break
                with timer.observation("stale_delivery"):
                    observation = adapter.observe()
                continue
            if mutation.status not in ("confirmed", "pending"):
                attempt.update(status=mutation.status, result=mutation.detail)
                reason = "error" if mutation.status == "uncertain" else "blocked"
                detail = mutation.detail
                break
            if mutation.status == "confirmed":
                # ADR-0047: a confirmed input after a dialog answer means the input the dialog held was finished.
                for earlier in mutations:
                    if earlier["status"] == "pending":
                        earlier.update(status="confirmed")
            attempt.update(status=mutation.status, result=mutation.input_method)
            stale_streak.clear()
            previous_semantic = observation.semantic_id
            acted_on = observation
            history.append(
                {"action": candidate.label, "kind": candidate.operation, "choice": candidate.id, "text": text}
            )
            if search is not None:
                search.delivered(candidate)
            try:
                with timer.observation("post_mutation"):
                    observation = adapter.observe()
            except Exception as exc:
                reason, detail = "error", f"post-mutation observation failed: {type(exc).__name__}"
                break
            if not observation.complete:
                reason, detail = "error", "post-mutation observation is incomplete"
                break
            # A wait is not an input, so it never counts toward the no-progress stop (agent.py).
            history[-1]["semantic_changed"] = (
                None if candidate.operation in non_mutating else observation.semantic_id != previous_semantic
            )
            if unchanged(acted_on, observation):
                ineffective.append((observation, candidate.id))
            elif observation.semantic_id != acted_on.semantic_id and observation.semantic_id in seen_states:
                cycling.add((acted_on.semantic_id, candidate.id))
            seen_states.add(observation.semantic_id)
            if self.scroll_progress_gate and candidate.operation not in non_mutating:
                measure = getattr(adapter, "scroll_progress", None)
                progressed = (
                    (candidate.attributes.get("direction"), candidate.attributes.get("rect"))
                    if candidate.operation == "scroll" and measure and measure(acted_on, observation, candidate)
                    else None
                )
            effect = effect_of(candidate, acted_on, observation, text) if effect_of is not None else None
            if effect is not None:
                # ADR-0039: Jev sees the fact in its history; the outer agent sees unmet ones in the goal result.
                history[-1]["effect"] = {key: effect[key] for key in ("expected", "after", "effect")}
                if effect["effect"] == "not_met":
                    unmet_effects.append(effect)
                    unmet.add((acted_on.semantic_id, candidate.id, text))
            if reach is not None:
                with timer("verify"):
                    # A click that starts a download is followed by provider doubt, not DONE; the wait costs nothing
                    # unless a download is still being written.
                    missing = reach(goal.until, observation, until_baseline, UNTIL_SETTLE_SECONDS)
                if not missing:
                    reason, last_verification = "outcome_verified", True
                    break
            if self.verifier is not None:
                try:
                    with timer("verify"):
                        last_verification = self.verifier(goal, observation)
                except Exception as exc:
                    reason, detail = "error", f"outcome verification failed: {type(exc).__name__}"
                    break
                if last_verification is True:
                    reason = "outcome_verified"
                    break
            if goal.app_expect is not None and last_verification is not False:
                with timer("verify"):
                    matched = _app_state_matches(goal.app_expect, acted_at, self.wall_clock, self._sleep)
                if matched:
                    reason, last_verification = "outcome_verified", True
                    break
            # With until, one action's achievement is progress; checking it would only spend provider calls.
            if self.action_verifier is not None and last_verification is not False and reach is None:
                goal_text = request.fill_values.get(goal.id)
                # Screen evidence costs an OCR pass on a frame UIA does not read (ADR-0036); only E's
                # measured operations and a send with known text read it. A launch is judged from window lists.
                evidence = getattr(adapter, "screen_evidence", None)
                if candidate.operation not in ARRIVAL_OPERATIONS and not goal_text:
                    evidence = None
                before_screen = evidence(acted_on, candidate) if evidence else None
                # BUG-0023: a popup item's click can close the popup itself; achievement E then needs the
                # root window's own before-state to tell "the anchor's value changed" from "it was already there".
                root_evidence = getattr(adapter, "root_evidence", None)
                before_anchor_screen = (
                    root_evidence(acted_on)
                    if evidence and root_evidence and candidate.attributes.get("scope") == "owned_popup"
                    else None
                )
                prior = tuple({"operation": item["kind"], "target_label": item["action"]} for item in history[:-1])
                verdict = None
                self._await_launch(candidate.operation, windows_before)
                try:
                    # A committed action often finishes drawing after the next capture (a sent message joins the
                    # list a moment later), so a changed screen is re-observed a bounded number of times.
                    for settle in range(COMMIT_SETTLE_OBSERVATIONS + 1):
                        if settle:
                            self._sleep(COMMIT_SETTLE_SECONDS)
                            judged = observation
                            with timer.observation("settle"):
                                observation = adapter.observe()
                            # A screen that stopped drawing would get the same verdict again; a late draw
                            # (E2E-I47) still differs from the previous capture.
                            if unchanged(judged, observation):
                                break
                        with timer("verify"):
                            verdict = self.action_verifier(
                                ActionOutcome(
                                    goal=goal.goal,
                                    candidate=candidate,
                                    text=text,
                                    before=acted_on,
                                    after=observation,
                                    before_screen=before_screen,
                                    after_screen=evidence(observation, candidate) if evidence else None,
                                    attempts=provider_attempts,
                                    attempt_limit=request.provider_attempt_budget,
                                    call_id=f"achieved-{len(history)}",
                                    windows_before=windows_before,
                                    windows_after=self._probe_windows() if windows_before is not None else None,
                                    goal_text=goal_text,
                                    prior_actions=prior,
                                    effect=effect,
                                    before_view=fit_view(acted_on) if fit_view else None,
                                    input_focus=mutation.input_focus,
                                    before_anchor_screen=before_anchor_screen,
                                )
                            )
                        if (
                            verdict is not None
                            or candidate.operation not in SETTLE_OPERATIONS
                            or not hasattr(adapter, "unchanged")
                            or unchanged(acted_on, observation)
                        ):
                            break
                except Exception as exc:
                    reason, detail = "error", f"outcome verification failed: {type(exc).__name__}"
                    break
                if verdict is True:
                    last_verification = True
                    reason = "outcome_verified"
                    break
            repeated = history[-3:]
            if len(repeated) == 3 and len({(h["choice"], h["text"]) for h in repeated}) == 1 and all(
                h["semantic_changed"] is False for h in repeated
            ):
                reason, detail = "blocked", "same action produced no semantic change three times"
                break

        outcome: OutcomeState = "unverified"
        if reason == "outcome_verified":
            outcome = "verified_success"
        elif reason != "error" and last_verification is False:
            outcome = "verified_failure"
        mutation_state = _mutation_state(mutations)
        result = self._goal_result(
            request,
            goal,
            reason,
            mutation_state,
            outcome,
            detail,
            metrics={
                "provider_attempts": len(provider_attempts),
                "decision_cache_hits": decision_cache_hits,
                "mutation_attempts": len(mutations),
                **({"direct_picks": direct_picks} if direct_picks else {}),
                **({"policy_actions": policy_actions} if policy_actions else {}),
                **({"scroll_progress_gates": progress_gates} if progress_gates else {}),
                **{f"provider_usage_{key}": value for key, value in sorted(provider_tokens.items())},
            },
            screen_candidates=screen_candidates,
            stage_ms=timer.result(),
            unmet_effects=tuple(unmet_effects),
        )
        if (
            outcome == "unverified"
            and mutation_state == "confirmed"
            and observation.complete
            and not request.target_id.startswith("browser:")
        ):
            result = replace(result, screen_changes=_screen_changes(goal_start, observation))
        self._event(request.run_id, reason, goal.id, detail)
        return result, observation

    def _await_launch(self, operation: str, windows_before) -> None:
        # BUG-0058: an app launched from Search shows its window a second or more later (Settings cold start); judged
        # earlier, the Enter looks ineffective and the next one reopens Search over the app.
        if not launch_pending(operation, windows_before, windows_before):
            return
        for _ in range(LAUNCH_POLLS):
            if not launch_pending(operation, windows_before, self._probe_windows()):
                return
            self._sleep(LAUNCH_POLL_SECONDS)

    def _probe_windows(self) -> tuple | None:
        if self.window_probe is None or self.action_verifier is None:
            return None
        try:
            return tuple(self.window_probe())
        except Exception:  # noqa: BLE001 - a missing window list only withholds the launch evidence
            return None

    def _goal_result(
        self,
        request,
        goal,
        reason,
        mutation_state,
        outcome,
        detail=None,
        metrics=None,
        screen_candidates=(),
        stage_ms=None,
        unmet_effects=(),
    ):
        return GoalResult(
            goal.id,
            reason,
            mutation_state,
            outcome,
            detail,
            f"finitact://runs/{request.run_id}/goals/{goal.id}",
            metrics or {},
            screen_candidates,
            stage_ms or {},
            unmet_effects,
        )

    def _cancelled(self, run_id):
        with self._lock:
            return self._entries[run_id]["cancelled"]

    def _event(self, run_id, reason, goal_id, detail):
        with self._lock:
            self._entries[run_id]["events"].append(
                {"goal_id": goal_id, "termination_reason": reason, "detail": detail}
            )

    def _save_entry(self, run_id, fingerprint, status, result):
        if not self.ledger_path:
            return
        payload = json.dumps(result.record()) if result else None
        with sqlite3.connect(self.ledger_path) as database:
            database.execute(
                "INSERT OR REPLACE INTO runs(run_id, fingerprint, status, result) VALUES (?, ?, ?, ?)",
                (run_id, fingerprint, status, payload),
            )

    def _load_entry(self, run_id):
        if not self.ledger_path:
            return None
        with sqlite3.connect(self.ledger_path) as database:
            row = database.execute(
                "SELECT fingerprint, status, result FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        fingerprint, status, payload = row
        result = None
        if status == "finished" and payload:
            record = json.loads(payload)
            result = RunResult(
                run_id=record["run_id"],
                status=record["status"],
                goals=tuple(GoalResult.from_record(goal) for goal in record["goals"]),
                remaining_goal_ids=tuple(record["remaining_goal_ids"]),
                journal_ref=record["journal_ref"],
                replayed=record.get("replayed", False),
            )
        events = [] if result is None else [
            {
                "goal_id": goal.goal_id,
                "termination_reason": goal.termination_reason,
                "detail": goal.detail,
            }
            for goal in result.goals
        ]
        entry = {
            "fingerprint": fingerprint,
            "cancelled": False,
            "events": events,
            "result": result,
            "interrupted": result is None,
        }
        self._entries[run_id] = entry
        return entry


class _Search:
    """find_and_click state for one goal (ADR-0027).

    The direction only moves forward (down, then up) and turns on an observed unchanged screen after a
    wheel, not on a missing candidate: the adapter reopens a list end whenever the other direction moves
    it, and OCR gaps can drop a candidate too. None hands the step back to the provider.
    """

    def __init__(self):
        self.direction = "down"
        self.last_scroll: str | None = None
        self.clicked = False

    def next(self, candidates, history):
        if self.clicked:
            return None
        if self.last_scroll == self.direction and history and history[-1].get("semantic_changed") is False:
            if self.direction == "up":
                return "exhausted"
            self.direction = "up"
        self.last_scroll = None
        clicks = [item for item in candidates if item.operation == "click"]
        if len(clicks) == 1:
            return clicks[0]
        scrolls = [item for item in candidates if item.operation == "scroll"]
        if len({json.dumps(item.attributes.get("rect")) for item in scrolls}) != 1:
            return None
        return next((item for item in scrolls if item.attributes.get("direction") == self.direction), None)

    def delivered(self, candidate):
        if candidate.operation == "click":
            self.clicked = True
        elif candidate.operation == "scroll":
            self.last_scroll = candidate.attributes.get("direction")


def _window_key(target_id: str) -> str:
    # A run on either path changes the window, so refs are kept per window, not per path.
    return target_id.split(":", 1)[-1]


def _goal_inputs(request: WindowsRunRequest, goal: Goal) -> dict:
    value = request.fill_values.get(goal.id)
    return {} if value is None else {"fill_value": value}


def _without_new_defaults(fields: dict) -> dict:
    """Drop EXP-0015 fields left unset, so runs recorded before them keep their fingerprints."""

    if not fields.get("app_annotations", True):
        del fields["app_annotations"]
    for goal in fields["goals"]:
        if goal.get("app_expect", True) is None:
            del goal["app_expect"]
    return fields


def _normalized(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _annotated(candidates, annotations: Sequence[AppAnnotation]) -> list[ObservedCandidate]:
    candidates = list(candidates)
    for note in annotations:
        hits = [index for index, item in enumerate(candidates) if _normalized(item.label) == _normalized(note.match_text)]
        # Uniqueness counts screen regions, not operations: one OCR line yields click and fill candidates that
        # describe the same control (EXP-0015 smoke). Two regions would put the app's fact on a control it may not describe.
        if len({_region(candidates[index]) for index in hits}) != 1:
            continue
        app = {"role": note.role, "name": note.name, "value": note.value, "source": note.source}
        for index in hits:
            item = candidates[index]
            candidates[index] = replace(item, attributes={**item.attributes, "app": app})
    return candidates


def _region(candidate: ObservedCandidate):
    rect = candidate.attributes.get("rect")
    return (candidate.subject, tuple(rect) if isinstance(rect, (list, tuple)) else None)


def _app_state_matches(expect: AppExpect, acted_at: float, wall_clock, sleep) -> bool:
    """True once the app's feed, written at least APP_EXPECT_SETTLE_SECONDS after the action, holds the value."""

    deadline = acted_at + APP_EXPECT_WAIT_SECONDS
    while True:
        try:
            state = json.loads(Path(expect.source).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = None
        if isinstance(state, dict) and isinstance(state.get("t"), (int, float)):
            if state["t"] >= acted_at + APP_EXPECT_SETTLE_SECONDS:
                return str(state.get(expect.key)) == expect.equals
        if wall_clock() >= deadline:
            return False
        sleep(0.1)


def _goal_candidates(request: WindowsRunRequest, goal: Goal, candidates) -> tuple[ObservedCandidate, ...]:
    allowed = tuple(item for item in candidates if item.operation in request.allowed_operations)
    label = request.click_label_constraints.get(goal.id)
    if label is None:
        return allowed
    matching = tuple(item for item in allowed if item.operation == "click" and item.label == label)
    permitted_click_id = matching[0].id if len(matching) == 1 else None
    return tuple(item for item in allowed if item.operation != "click" or item.id == permitted_click_id)
