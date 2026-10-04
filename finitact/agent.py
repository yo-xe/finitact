"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .contracts import decision_request
from .model import ProviderBudgetExceeded, action_space, field_context
from .providers import OpenAITextHelper, TypeSafeDecisionProvider
from .questions import MAX_STEPS

MAX_MODEL_ATTEMPTS = MAX_STEPS * 2


class Agent:
    def __init__(
        self,
        url,
        goals,
        *,
        record_dir=None,
        screenshots=False,
        decision_provider=None,
        text_helper=None,
        browser_factory=Browser,
        action_budget=MAX_STEPS,
        provider_attempt_budget=MAX_MODEL_ATTEMPTS,
    ):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        self.decision_provider = decision_provider or TypeSafeDecisionProvider()
        self.text_helper = text_helper or OpenAITextHelper()
        self.browser = browser_factory(url)
        self.action_budget = action_budget
        self.provider_attempt_budget = provider_attempt_budget
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            model_attempts=[],
            mutation_attempts=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                repeated = state["decisions"][-3:]
                if len(repeated) == 3 and len({d["choice"] for d in repeated}) == 1:
                    state["status"] = "blocked"
                    raise ValueError("Stopped after the same decision became stale 3 times")
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a run first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh run.")
            attempts = state.setdefault("model_attempts", [])
            provider_attempt_budget = getattr(self, "provider_attempt_budget", MAX_MODEL_ATTEMPTS)
            if len(attempts) >= provider_attempt_budget:
                state["status"] = "blocked"
                raise ValueError("Reached the run's provider-attempt budget")
            try:
                request = decision_request(
                    state["page"], state["goal"], state["history"], provider_attempt_budget - len(attempts)
                )
                state["decision"] = self.decision_provider.decide(
                    request,
                    attempts=attempts,
                    call_id=f"decision-{len(state['decisions']) + 1}",
                ).state_record()
            except ProviderBudgetExceeded:
                state["status"] = "blocked"
                raise
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            action_budget = getattr(self, "action_budget", MAX_STEPS)
            if len(state["history"]) >= action_budget:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {action_budget}-action run budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    attempts = state.setdefault("model_attempts", [])
                    try:
                        text, helper = self.text_helper.generate(
                            context,
                            attempts=attempts,
                            call_id=f"text-{len(state['text_calls']) + 1}",
                            attempt_limit=getattr(self, "provider_attempt_budget", MAX_MODEL_ATTEMPTS),
                        )
                    except ProviderBudgetExceeded:
                        state["status"] = "blocked"
                        raise
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            attempt = None
            if action["kind"] != "wait":
                attempts = state.setdefault("mutation_attempts", [])
                attempt = {
                    "attempt_id": len(attempts) + 1,
                    "decision_id": len(state["decisions"]),
                    "choice": selected,
                    "kind": action["kind"],
                    "action": action["label"],
                    "text": text,
                    "page_fingerprint": page["fingerprint"],
                    "status": "attempted",
                    "result": None,
                    "attempted_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
                attempts.append(attempt)
            try:
                result = state["browser"].act(action, page, text=text)
            except StalePage:
                if attempt:
                    attempt.update(status="not_attempted", result="stale_before_input")
                raise
            except Exception as exc:
                if attempt:
                    attempt.update(status="uncertain", result=type(exc).__name__)
                state["status"] = "blocked"
                raise
            if attempt:
                attempt.update(status="confirmed", result=result)
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision.get("probabilities", {}).get(selected),
                    "confidence": decision.get("confidence"),
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision.get("operation"),
                    "target": decision.get("target"),
                    "page_changed": None,
                    "semantic_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                semantic_changed=(
                    state["page"]["semantic_fingerprint"] != page["semantic_fingerprint"]
                ),
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3
                and len({(h["action"], h["kind"], h["text"]) for h in repeated}) == 1
                and all(h["semantic_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def continue_with(self, goal):
        """Start the next ordered goal without discarding the browser session."""
        task = goal.strip()
        if not task:
            raise ValueError("Supply a task")
        self.pending_text = None
        self.state.update(goal=task, decision=None, status="ready")
        self.state["plan"].append(task)
        self.state["plan_index"] = len(self.state["plan"]) - 1
        return self.snapshot()

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
