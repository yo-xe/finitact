"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import threading
import time
from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest

from finitact import agent as loop
from finitact import model
from finitact.browser import (
    Browser,
    MutationUncertain,
    StalePage,
    browser_operation,
    fingerprint,
    semantic_fingerprint,
)
from finitact.contracts import Decision, decision_request
from finitact.providers import OllamaDecisionProvider, TypeSafeDecisionProvider


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    state["semantic_fingerprint"] = semantic_fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_provider_neutral_request_contains_candidates_history_and_remaining_budget():
    observed = page()
    request = decision_request(observed, "Find a book", [{"action": "Search"}], 7)

    assert request.goal == "Find a book"
    assert request.observation_id == observed["fingerprint"]
    assert request.remaining_attempts == 7
    assert request.history == ({"action": "Search"},)
    assert request.candidates[0].id == "e1"
    assert request.candidates[0].operation == "fill"
    assert request.candidates[0].subject == "10"
    assert "rect" not in request.candidates[0].attributes
    assert request.untrusted_context["text"] == "Search"


def test_typesafe_adapter_keeps_provider_specific_metadata_optional(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    raw = decision("e3") | {
        "operation_probabilities": {"CLICK": 1.0},
        "target_probabilities": {"2": 1.0},
        "raw_answers": {"operation": {}},
    }
    choose = Mock(return_value=raw)
    monkeypatch.setattr(model, "choose", choose)
    request = decision_request(page(), "Find a book", [], 9)

    result = TypeSafeDecisionProvider().decide(request, attempts=[], call_id="decision-1")

    assert result.choice == "e3" and result.terminal_reason is None
    assert result.measurements == {"latency_ms": 10, "usage": {}}
    assert result.provider_metadata["operation_probabilities"] == {"CLICK": 1.0}
    sent_page = choose.call_args.args[0]
    assert sent_page["actions"][0]["node"] == "10"
    assert choose.call_args.kwargs["attempt_limit"] == 9


def test_ollama_adapter_constrains_output_to_observed_choices_and_records_local_usage(monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "qwen2.5:14b-instruct",
                "message": {"content": json.dumps({"choice": "e3", "reason": "Go advances the goal"})},
                "prompt_eval_count": 321,
                "eval_count": 12,
                "total_duration": 1000,
            }

    post = Mock(return_value=Response())
    monkeypatch.setattr("finitact.providers.httpx.post", post)
    attempts = []
    result = OllamaDecisionProvider().decide(
        decision_request(page(), "Find a book", [], 4), attempts=attempts, call_id="decision-1"
    )

    assert result.choice == "e3"
    assert result.measurements["usage"] == {"prompt_tokens": 321, "completion_tokens": 12, "external_bytes": 0}
    sent = post.call_args.kwargs["json"]
    assert sent["format"]["properties"]["choice"]["enum"] == ["e1", "e2", "e3", "wait", "DONE", "BLOCKED"]
    assert sent["truncate"] is False and sent["shift"] is False
    assert "UNTRUSTED_PAGE" in sent["messages"][0]["content"]
    assert attempts[0]["status"] == "confirmed"


def test_ollama_context_overflow_fails_closed_and_records_prompt_size(monkeypatch):
    # Body shape measured on Ollama 0.31.2 with truncate=false (BUG-0019).
    error = {"error": {"code": 400, "message": "request exceeds", "type": "exceed_context_size_error",
                       "n_prompt_tokens": 17949, "n_ctx": 8192}}
    response = httpx.Response(
        400, json={"error": json.dumps(error)}, request=httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
    )
    monkeypatch.setattr("finitact.providers.httpx.post", Mock(return_value=response))
    attempts = []
    with pytest.raises(RuntimeError, match="no action executed"):
        OllamaDecisionProvider().decide(
            decision_request(page(), "Find a book", [], 4), attempts=attempts, call_id="decision-1"
        )
    assert attempts[0]["status"] == "error"
    assert (attempts[0]["prompt_tokens"], attempts[0]["context_tokens"]) == (17949, 8192)


def test_safe_url_redacts_data_uris_but_passes_through_http_and_blob():
    assert model.safe_url("https://example.test/inventory.html") == "https://example.test/inventory.html"
    redacted = model.safe_url("data:text/html,%3Chtml%3Esecret+content%3C/html%3E")
    assert redacted.startswith("data:text/html,")
    assert "secret" not in redacted
    # blob: is an opaque in-memory reference, not embedded content -- nothing to redact.
    assert model.safe_url("blob:https://example.test/abc-123") == "blob:https://example.test/abc-123"


@pytest.mark.parametrize(
    ("operation", "last", "expected"),
    [
        ("BLOCKED", {"kind": "click", "semantic_changed": True}, True),
        ("DONE", {"kind": "click", "semantic_changed": True}, False),  # DONE itself is not flagged
        ("BLOCKED", {"kind": "wait", "semantic_changed": True}, False),  # own action must be a mutation
        ("BLOCKED", {"kind": "click", "semantic_changed": False}, False),  # page must have actually changed
    ],
)
def test_diagnose_flags_non_done_right_after_own_mutation(operation, last, expected):
    flags = model.diagnose(operation, [last], "Some page")
    assert ("post_mutation_non_done" in flags) is expected


@pytest.mark.parametrize(
    ("history_text", "page_text", "expected"),
    [
        ("Payment confirmed", "Pay Now", True),  # fabricated: page never repeats the claim
        ("Payment confirmed", "Payment confirmed. Receipt #123.", False),  # page corroborates it
        (None, "Pay Now", False),  # no claim in history at all
    ],
)
def test_diagnose_flags_done_that_relies_on_an_uncorroborated_history_claim(history_text, page_text, expected):
    entry = {"kind": "wait", "text": history_text, "semantic_changed": True}
    flags = model.diagnose("DONE", [entry], page_text)
    assert ("done_relies_on_history_claim" in flags) is expected


def test_choose_sends_redacted_url_for_data_uri_but_full_text_and_actions(monkeypatch):
    observed = page()
    observed["url"] = "data:text/html,%3Chtml%3Esecret+decoy+content%3C/html%3E"
    calls = []

    def post(_url, _key, body, **_kwargs):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(observed, "Find a book", [])
    sent_page = calls[0]["state"]["page"]
    assert "secret" not in sent_page["url"]
    assert sent_page["text"] == observed["text"]  # text/actions are not touched by the url fix


def test_choose_sends_real_url_unchanged_for_ordinary_https_page(monkeypatch):
    observed = page()
    calls = []

    def post(_url, _key, body, **_kwargs):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(observed, "Find a book", [])
    assert calls[0]["state"]["page"]["url"] == observed["url"]


def test_choose_attaches_diagnostics_without_changing_the_sent_request(monkeypatch):
    observed = page()
    history = [{"kind": "click", "text": "Payment confirmed", "semantic_changed": True}]
    calls = []

    def post(_url, _key, body, **_kwargs):
        calls.append(body)
        return {
            "model": "test",
            "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "BLOCKED")},
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(observed, "Find a book", history)
    assert d["diagnostics"] == {
        "post_mutation_non_done": "The agent's own last action changed the page, but this decision is not DONE."
    }
    assert "diagnostics" not in calls[0]  # diagnostics are derived after the response, never sent


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []
    observed = page()
    observed["unsupported"] = {"frames": 1}

    def post(_url, _key, body, **_kwargs):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(observed, "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}
    assert calls[0]["state"]["page"]["unsupported"] == {"frames": 1}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body, **_kwargs):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body, **_kwargs):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Aurora"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Enter "Aurora" in the city field', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Aurora"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Enter "Aurora" in the city field'


def test_openai_text_helper_uses_minimum_reasoning_and_output_budget(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("TEXT_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("TEXT_MODEL_REASONING", "none")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Aurora"}'}}]})
    monkeypatch.setattr(model, "post_json", post)

    assert model.field_text({"goal": "Enter Aurora"})[0] == "Aurora"
    body = post.call_args.args[2]
    assert body["reasoning_effort"] == "none"
    assert body["max_completion_tokens"] == 64
    assert "reasoning" not in body and "max_tokens" not in body and "temperature" not in body


def test_deepseek_text_helper_is_greedy(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.delenv("TEXT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("TEXT_MODEL_REASONING", raising=False)
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Aurora"}'}}]})
    monkeypatch.setattr(model, "post_json", post)

    model.field_text({"goal": 'Enter "Aurora"'})
    assert post.call_args.args[2]["temperature"] == 0


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Aurora"'})


def test_provider_attempt_journal_records_retries_without_request_or_credentials(monkeypatch):
    def response(status, payload):
        value = Mock(status_code=status, is_error=status >= 400)
        value.json.return_value = payload
        return value

    post = Mock(side_effect=[
        response(503, {"error": {"message": "temporary"}}),
        response(200, {"answers": {}, "model": "jev-test", "usage": {}}),
    ])
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", Mock())
    audit = []
    body = {
        "model": "jev-test",
        "state": {"private": "must-not-be-journaled"},
        "questions": {"operation": {"type": "choice"}},
    }

    result = model.post_json(
        "https://api.typesafe.ai/v1/systemone",
        "secret-key",
        body,
        audit=audit,
        call_id="decision-1",
        provider="typesafe",
    )

    assert result["model"] == "jev-test"
    assert [entry["status"] for entry in audit] == ["retryable_error", "confirmed"]
    assert [entry["http_status"] for entry in audit] == [503, 200]
    assert audit[0]["will_retry"] is True and audit[1]["will_retry"] is False
    assert audit[0]["request_sha256"] == audit[1]["request_sha256"]
    assert audit[0]["question_ids"] == ["operation"]
    serialized = json.dumps(audit)
    assert "secret-key" not in serialized and "must-not-be-journaled" not in serialized


def test_provider_attempt_journal_records_connection_failure(monkeypatch):
    monkeypatch.setattr(
        model.CLIENT,
        "post",
        Mock(side_effect=model.httpx.ConnectError("offline")),
    )
    audit = []

    with pytest.raises(RuntimeError, match="connection failed"):
        model.post_json(
            "https://api.typesafe.ai/v1/systemone",
            "secret-key",
            {"model": "jev-test", "state": "safe", "questions": {}},
            audit=audit,
            call_id="decision-1",
            provider="typesafe",
        )

    assert audit[0]["status"] == "connection_error"
    assert audit[0]["error"] == "ConnectError"
    assert audit[0]["http_status"] is None


def test_provider_attempt_budget_counts_retries_and_stops_before_next_request(monkeypatch):
    response = Mock(status_code=503, is_error=True)
    response.json.return_value = {"error": {"message": "temporary"}}
    post = Mock(return_value=response)
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", Mock())
    audit = []

    with pytest.raises(RuntimeError, match="provider-attempt budget"):
        model.post_json(
            "https://api.typesafe.ai/v1/systemone",
            "secret-key",
            {"model": "jev-test", "state": "safe", "questions": {}},
            audit=audit,
            call_id="decision-1",
            provider="typesafe",
            attempt_limit=2,
        )

    assert len(audit) == 2
    assert post.call_count == 2
    assert all(entry["status"] == "retryable_error" for entry in audit)


def test_text_helper_attempt_budget_blocks_run_before_browser_input(runner, monkeypatch):
    runner.text_helper.generate = Mock(
        side_effect=model.ProviderBudgetExceeded("budget reached")
    )

    with pytest.raises(model.ProviderBudgetExceeded):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    assert runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    a.decision_provider = Mock()
    a.text_helper = Mock()
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
        "model_attempts": [],
        "mutation_attempts": [],
    }
    return a


def test_agent_executes_provider_neutral_decision_without_probabilities(runner):
    runner.state["decision"] = None
    runner.state["status"] = "ready"
    runner.decision_provider.decide.return_value = Decision(
        choice="e3", measurements={"latency_ms": 4, "usage": {"local": True}}
    )

    predicted = runner.command("predict")
    assert predicted["decision"]["choice"] == "e3"
    assert "probabilities" not in predicted["decision"]

    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    history = runner.state["history"][-1]
    assert history["choice"] == "e3"
    assert history["probability"] is None and history["confidence"] is None
    runner.state["browser"].act.assert_called_once()


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None
    assert runner.state["mutation_attempts"] == []


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    runner.text_helper.generate = helper
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    runner.text_helper.generate = helper
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_dom_identity_churn_does_not_hide_repeated_semantic_no_progress(runner):
    observations = []
    for node in (101, 102, 103):
        observed = page()
        observed["actions"][2]["node"] = node
        observed["fingerprint"] = fingerprint(observed)
        observed["semantic_fingerprint"] = semantic_fingerprint(observed)
        observations.append(observed)
    runner.state["browser"].observe.side_effect = observations

    for _ in range(3):
        runner.state["decision"] = decision("e3")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    assert all(h["page_changed"] is True for h in runner.state["history"])
    assert all(h["semantic_changed"] is False for h in runner.state["history"])
    assert runner.state["status"] == "blocked"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()
    assert runner.state["mutation_attempts"][-1]["status"] == "confirmed"


def test_stale_before_browser_input_is_recorded_as_not_attempted(runner, monkeypatch):
    runner.text_helper.generate = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    runner.state["browser"].act.side_effect = StalePage("changed before input")

    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    assert runner.state["mutation_attempts"][-1]["status"] == "not_attempted"
    assert runner.state["mutation_attempts"][-1]["result"] == "stale_before_input"


def test_partial_mutation_failure_is_uncertain_and_stops_run(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].act.side_effect = RuntimeError("mouse release failed")

    with pytest.raises(RuntimeError, match="mouse release"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    attempt = runner.state["mutation_attempts"][-1]
    assert attempt["status"] == "uncertain"
    assert attempt["result"] == "RuntimeError"
    assert runner.state["status"] == "blocked"
    assert runner.state["history"] == []


def test_wait_does_not_create_a_mutation_attempt(runner):
    runner.state["decision"] = decision("wait")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["mutation_attempts"] == []


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import finitact.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import finitact.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


def test_click_rebinds_one_semantically_identical_re_rendered_target(monkeypatch):
    import finitact.browser as browser

    old = page()
    old["page_key"] = [1, old["url"], 0, 0, 1120, 780, [[10, "", False, -1, False, False]]]
    old["guards"] = {"20": [20, "button", "Go", "", None, None, None, False, None, None,
                                    None, None, None, "Search\nGo"]}
    action = old["actions"][2]
    current = deepcopy(old)
    current["page_key"][6][0][0] = 110
    current["actions"][2]["node"] = 220
    current["guards"] = {"220": [220, *old["guards"]["20"][1:]]}

    b = Browser.__new__(Browser)
    b.session = "test"
    b.evaluate = Mock(return_value=[current["page_key"], None])
    monkeypatch.setattr(browser, "browser_operation", Mock(return_value=current))

    assert b.fresh(old, action)
    assert action["node"] == 220


def test_click_does_not_rebind_an_ambiguous_or_changed_target(monkeypatch):
    import finitact.browser as browser

    old = page()
    old["page_key"] = [1, old["url"], 0, 0, 1120, 780, []]
    old["guards"] = {"20": [20, "button", "Go", "", None, None, None, False, None, None,
                                    None, None, None, "Search\nGo"]}
    action = old["actions"][2]
    current = deepcopy(old)
    duplicate = deepcopy(current["actions"][2])
    current["actions"][2]["node"] = 220
    duplicate["node"] = 221
    current["actions"].append(duplicate)
    current["guards"] = {
        "220": [220, *old["guards"]["20"][1:]],
        "221": [221, *old["guards"]["20"][1:]],
    }

    b = Browser.__new__(Browser)
    b.session = "test"
    b.evaluate = Mock(return_value=[current["page_key"], None])
    monkeypatch.setattr(browser, "browser_operation", Mock(return_value=current))

    assert not b.fresh(old, action)
    assert action["node"] == 20


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import finitact.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(MutationUncertain):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


@pytest.mark.parametrize(
    ("action", "text", "responses", "failed_method"),
    [
        (
            {"id": "e1", "kind": "click", "node": 1},
            None,
            [{"result": {"value": {"x": 10, "y": 10}}}, RuntimeError("press failed")],
            "Input.dispatchMouseEvent",
        ),
        (
            {"id": "e1", "kind": "click", "node": 1},
            None,
            [{"result": {"value": {"x": 10, "y": 10}}}, {}, RuntimeError("release failed")],
            "Input.dispatchMouseEvent",
        ),
        (
            {"id": "e1", "kind": "fill", "node": 1},
            "book",
            [
                {"result": {"value": {"x": 10, "y": 10}}},
                {},
                {},
                RuntimeError("key down failed"),
            ],
            "Input.dispatchKeyEvent",
        ),
        (
            {"id": "e1", "kind": "fill", "node": 1},
            "book",
            [
                {"result": {"value": {"x": 10, "y": 10}}},
                {},
                {},
                {},
                RuntimeError("key up failed"),
            ],
            "Input.dispatchKeyEvent",
        ),
        (
            {"id": "e1", "kind": "fill", "node": 1},
            "book",
            [
                {"result": {"value": {"x": 10, "y": 10}}},
                {},
                {},
                {},
                {},
                RuntimeError("insert failed"),
            ],
            "Input.insertText",
        ),
        (
            {"id": "scroll", "kind": "scroll", "delta": 500},
            None,
            [RuntimeError("wheel failed")],
            "Input.dispatchMouseEvent",
        ),
    ],
)
def test_cdp_mutation_substep_failure_is_never_safe_to_retry(
    monkeypatch, action, text, responses, failed_method
):
    import finitact.browser as browser

    cdp = Mock(side_effect=responses)
    monkeypatch.setattr(browser, "cdp", cdp)

    with pytest.raises(MutationUncertain, match="mutation result is unknown"):
        browser_operation({
            "operation": "act", "session": "test", "action": action, "text": text,
        })

    assert cdp.call_args.args[0] == failed_method


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)
    assert semantic_fingerprint(p) == semantic_fingerprint(other)


def test_semantic_fingerprint_tracks_meaning_not_geometry():
    p = page()
    other = deepcopy(p)
    other["actions"][0]["rect"] = {"x": 10, "y": 20, "w": 30, "h": 40}
    assert semantic_fingerprint(p) == semantic_fingerprint(other)
    other["actions"][0]["value"] = "changed"
    assert semantic_fingerprint(p) != semantic_fingerprint(other)


@pytest.mark.parametrize(
    "content", ["Thinking: Aurora", '{"text":null}', '{"text":"Aurora","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Enter a city"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_same_stale_decision_stops_after_three_unexecuted_attempts(runner, monkeypatch):
    runner.decision_provider.decide = Mock(
        return_value=Decision(choice="e3", measurements={"latency_ms": 10})
    )
    runner.state["decision"] = None
    runner.state["status"] = "ready"
    runner.state["browser"].fresh.return_value = True
    runner.state["browser"].act.side_effect = StalePage("re-rendered")

    runner.command("tick")
    runner.command("tick")
    with pytest.raises(ValueError, match="same decision became stale 3 times"):
        runner.command("tick")

    assert runner.state["status"] == "blocked"
    assert len(runner.state["decisions"]) == 3
    assert runner.state["history"] == []


def _observed_request(count, remaining_attempts=3):
    from finitact.contracts import DecisionRequest, ObservedCandidate

    candidates = tuple(
        ObservedCandidate(f"c{index}", "click", f"item {index}", f"ref-{index}", {}) for index in range(count)
    )
    return DecisionRequest(
        goal="Switch workspace",
        observation_id="obs-1",
        candidates=candidates,
        untrusted_context={"control_values": {}},
        history=(),
        remaining_attempts=remaining_attempts,
    )


def _answer_first(winner=None):
    def post(url, key, body, *, audit, call_id, provider, attempt_limit):
        if len(audit) >= attempt_limit:
            raise model.ProviderBudgetExceeded("budget")
        audit.append({"call_id": call_id})
        ids = list(body["questions"]["choice"]["criteria"])
        choice = winner(ids) if winner else ids[0]
        probabilities = {key: 1.0 if key == choice else 0.0 for key in ids}
        return {
            "answers": {"choice": {"choice": choice, "confidence": 1.0, "probabilities": probabilities}},
            "model": "jev-test",
            "usage": {"input_tokens": 10},
        }

    return Mock(side_effect=post)


@pytest.fixture
def typesafe_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")


def test_typesafe_keeps_a_single_question_up_to_the_choice_limit(monkeypatch, typesafe_key):
    post = _answer_first()
    monkeypatch.setattr(model, "post_json", post)

    raw = model.choose_observed_candidates(_observed_request(253), attempts=[], call_id="d1", attempt_limit=3)

    assert post.call_count == 1
    assert len(post.call_args.args[2]["questions"]["choice"]["criteria"]) == 255
    assert "heats" not in raw


def test_typesafe_over_the_choice_limit_runs_heats_then_a_final_with_done_and_blocked(monkeypatch, typesafe_key):
    post = _answer_first(lambda ids: "c300" if "c300" in ids else ids[-1])
    monkeypatch.setattr(model, "post_json", post)
    attempts = []

    raw = model.choose_observed_candidates(_observed_request(351), attempts=attempts, call_id="d1", attempt_limit=3)

    heats, final = [call.args[2] for call in post.call_args_list[:2]], post.call_args_list[2].args[2]
    assert sorted(len(body["questions"]["choice"]["criteria"]) for body in heats) == [175, 176]
    assert all("DONE" not in body["questions"]["choice"]["criteria"] for body in heats)
    assert all("all_observed_labels" not in body["state"] for body in heats)
    assert len(final["state"]["all_observed_labels"]) == 351
    assert list(final["questions"]["choice"]["criteria"]) == ["c175", "c300", "DONE", "BLOCKED"]
    assert raw["choice"] == "c300"
    assert raw["usage"] == {"input_tokens": 30}
    assert [heat["choice"] for heat in raw["heats"]] == ["c175", "c300"]
    assert len(attempts) == 3


def test_typesafe_heats_draw_on_the_shared_budget_and_never_act_on_a_partial_result(monkeypatch, typesafe_key):
    monkeypatch.setattr(model, "post_json", _answer_first())

    with pytest.raises(model.ProviderBudgetExceeded):
        model.choose_observed_candidates(_observed_request(351), attempts=[], call_id="d1", attempt_limit=2)


def test_typesafe_heats_run_concurrently(monkeypatch, typesafe_key):
    both_started = threading.Barrier(2, timeout=5)
    answer = _answer_first()

    def post(url, key, body, **kwargs):
        if "DONE" not in body["questions"]["choice"]["criteria"]:
            both_started.wait()  # a sequential loop would never release the first heat
        return answer(url, key, body, **kwargs)

    monkeypatch.setattr(model, "post_json", post)

    raw = model.choose_observed_candidates(_observed_request(351), attempts=[], call_id="d1", attempt_limit=3)

    assert len(raw["heats"]) == 2


def test_heat_chunks_rotation_moves_the_boundaries():
    ids = [f"c{index}" for index in range(10)]

    assert model.heat_chunks(ids, 4) == [ids[0:4], ids[4:7], ids[7:10]]
    assert model.heat_chunks(ids, 4, rotation=2)[0] == ["c2", "c3", "c4", "c5"]


def test_post_json_surfaces_fastapi_detail_from_http_errors(monkeypatch):
    response = Mock(status_code=400, is_error=True)
    response.json.return_value = {"detail": "Too many choices. Must have at most 255 choices."}
    monkeypatch.setattr(model.CLIENT, "post", Mock(return_value=response))

    with pytest.raises(RuntimeError, match="HTTP 400: Too many choices"):
        model.post_json("https://api.typesafe.ai/v1/systemone", "key", {"questions": {}})
