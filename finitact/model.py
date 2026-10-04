"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import hashlib
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .questions import FINAL_ACTION, HEAT_ACTION, NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


# Heats post concurrently; the budget check and the audit append must be one step or both could pass.
_AUDIT_LOCK = threading.Lock()


class ProviderBudgetExceeded(RuntimeError):
    """No provider request was sent because the run-wide attempt limit was reached."""


class ProviderInputTooLarge(RuntimeError):
    """No provider request was sent because the input exceeds a documented provider limit."""


# SystemOne answers HTTP 400 "Too many choices. Must have at most 255 choices." (measured 2026-09-24).
TYPESAFE_MAX_CHOICES = 255


def post_json(url, key, body, *, audit=None, call_id=None, provider="model", attempt_limit=None):
    request_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    for attempt in range(3):
        entry = None
        started = time.perf_counter()
        if audit is not None:
            with _AUDIT_LOCK:
                if attempt_limit is not None and len(audit) >= attempt_limit:
                    raise ProviderBudgetExceeded("Reached the run's provider-attempt budget; no request sent.")
                entry = {
                    "call_id": call_id,
                    "provider": provider,
                    "attempt": attempt + 1,
                    "model": body.get("model"),
                    "request_sha256": request_hash,
                    "question_ids": sorted(body.get("questions", {})),
                    "status": "attempted",
                    "http_status": None,
                    "will_retry": False,
                    "latency_ms": None,
                }
                audit.append(entry)
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            if entry:
                entry.update(
                    status="connection_error",
                    error=type(exc).__name__,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            if entry:
                entry.update(
                    status="retryable_error",
                    http_status=response.status_code,
                    will_retry=True,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            if entry:
                entry.update(
                    status="http_error",
                    http_status=response.status_code,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
            try:
                payload = response.json()
                error = payload.get("error")
                # SystemOne reports validation failures FastAPI-style as {"detail": ...}.
                detail = error.get("message", "") if isinstance(error, dict) else payload.get("detail", "")
                detail = detail if isinstance(detail, str) else json.dumps(detail)
            except (ValueError, TypeError, AttributeError):
                detail = ""
            suffix = f": {detail[:300]}" if detail else ""
            raise RuntimeError(
                f"Model provider returned HTTP {response.status_code}{suffix}; no action executed."
            )
        if entry:
            entry.update(
                status="confirmed",
                http_status=response.status_code,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def safe_url(url):
    """A `data:` URI embeds the page's own content in the URL string itself -- a channel the model
    reads and factors into its decision separately from `page.text`/`page.actions` (confirmed by
    fault injection: an identical candidate state flips BLOCKED->DONE purely from a `data:` URL's
    embedded HTML, see docs/evaluations/browser-blind-spots/phase6-repro.md bisections B14-B19).
    A real `http(s)://` URL (or `blob:`, which is an opaque in-memory reference, not embedded
    content) is an inert identifier with no such payload and passes through unchanged.
    """
    if not url.lower().startswith("data:"):
        return url
    mime = url[len("data:"):].split(",", 1)[0].split(";", 1)[0] or "unknown"
    return f"data:{mime},[inline document, {len(url)} chars omitted]"


def diagnose(operation, history, page_text):
    """Flag patterns from Phase 6's blind-spot audit without changing what gets executed.

    Both are known Jev/TypeSafe judgment characteristics, not `finitact` defects
    (docs/evaluations/browser-blind-spots/phase6-candidates.md): surfacing them for review is
    the adopted mitigation since no safe generic execution-layer fix exists for either.
    """
    flags = {}
    if history:
        last = history[-1]
        if operation != "DONE" and last.get("kind") != "wait" and last.get("semantic_changed") is True:
            flags["post_mutation_non_done"] = (
                "The agent's own last action changed the page, but this decision is not DONE."
            )
    if operation == "DONE":
        page_lower = (page_text or "").lower()
        for entry in history:
            claim = entry.get("text")
            if claim and claim.strip().lower() not in page_lower:
                flags["done_relies_on_history_claim"] = (
                    "DONE was chosen while a history claim is uncorroborated by the current page text."
                )
                break
    return flags


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        # A JavaScript dialog's buttons are not page nodes (ADR-0047); each is its own element.
        node = action.get("node", action["id"])
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history, *, attempts=None, call_id=None, attempt_limit=None):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL") or "jev-latest",
        "state": {
            "page": {
                "url": safe_url(state["url"]),
                **{k: state[k] for k in ("title", "text")},
                "unsupported": state.get("unsupported", {}),
            },
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed", "semantic_changed")}
                for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json(
        "https://api.typesafe.ai/v1/systemone",
        os.environ["TYPESAFE_API_KEY"],
        body,
        audit=attempts,
        call_id=call_id,
        provider="typesafe",
        attempt_limit=attempt_limit,
    )
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
        "diagnostics": diagnose(operation, history, state["text"]),
    }


TERMINAL_CHOICES = {
    "DONE": {"meaning": "Every requirement is visibly satisfied."},
    "BLOCKED": {"meaning": "No supported operation can progress."},
}


def heat_chunks(ids, limit, rotation=0):
    """Split ids into the fewest near-equal contiguous chunks of at most `limit`.

    `rotation` shifts the chunk boundaries so a measurement can check whether the outcome depends on them.
    """
    ids = list(ids)
    count = -(-len(ids) // limit)
    if rotation:
        shift = rotation % len(ids)
        ids = ids[shift:] + ids[:shift]
    size, extra = divmod(len(ids), count)
    chunks, start = [], 0
    for index in range(count):
        stop = start + size + (index < extra)
        chunks.append(ids[start:stop])
        start = stop
    return chunks


def _systemone_choice(state, choices, rules, goal, *, attempts, call_id, attempt_limit):
    body = {
        "model": os.environ.get("TYPESAFE_MODEL") or "jev-latest",
        "state": state,
        "questions": {
            "choice": {
                "type": "choice",
                "criteria": choices,
                "instructions": {"goal": goal, "rules": rules},
            }
        },
    }
    started = time.perf_counter()
    result = post_json(
        "https://api.typesafe.ai/v1/systemone",
        os.environ["TYPESAFE_API_KEY"],
        body,
        audit=attempts,
        call_id=call_id,
        provider="typesafe",
        attempt_limit=attempt_limit,
    )
    answer = validate_choice(result["answers"].get("choice", {}), choices)
    return body, result, answer, round((time.perf_counter() - started) * 1000)


def _sum_usage(usages):
    totals = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals


def choose_observed_candidates(request, *, attempts=None, call_id=None, attempt_limit=None, heat_rotation=0):
    """Choose one adapter-neutral observed candidate through TypeSafe SystemOne.

    Over the choice limit, contiguous chunks each nominate one candidate without DONE/BLOCKED, and a
    final question over the nominees plus DONE/BLOCKED decides (BUG-0021, docs/consult/20260924-1304-bug0021-consult.md).
    Every request draws on the same attempt budget; only the final answer can lead to an action.
    """

    candidates = {
        candidate.id: {
            "operation": candidate.operation,
            "label": candidate.label,
            "attributes": dict(candidate.attributes),
            "current_value": request.untrusted_context.get("control_values", {}).get(candidate.id),
        }
        for candidate in request.candidates
    }
    # `criteria` already carries each offered record; repeating all of them in state doubled the input
    # and hit SystemOne's max_tokens_exceeded at 210 screen candidates (BUG-0022).
    state = {
        "untrusted_context": dict(request.untrusted_context),
        "recent_actions": list(request.history),
        **({"goal_inputs": dict(request.goal_inputs)} if request.goal_inputs else {}),
    }
    call = dict(attempts=attempts, call_id=call_id, attempt_limit=attempt_limit)
    limit = TYPESAFE_MAX_CHOICES - len(TERMINAL_CHOICES)
    heats, finalists, rules = [], candidates, NEXT_ACTION
    if len(candidates) > limit:
        chunks = heat_chunks(candidates, limit, heat_rotation)
        if len(chunks) > limit:
            raise ProviderInputTooLarge(
                f"{len(candidates)} candidates need {len(chunks)} finalists, over the TypeSafe limit; no action executed."
            )
        if attempts is not None and attempt_limit is not None and len(attempts) + len(chunks) + 1 > attempt_limit:
            raise ProviderBudgetExceeded("The heats and final do not fit the provider-attempt budget; no request sent.")

        def heat(chunk):
            return _systemone_choice(state, {key: candidates[key] for key in chunk}, HEAT_ACTION, request.goal, **call)

        # Heats are independent, so they run concurrently; only the final can lead to an action.
        with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
            answered = list(pool.map(heat, chunks))
        for chunk, (_, result, answer, latency) in zip(chunks, answered):
            heats.append({
                "candidates": len(chunk),
                "first": chunk[0],
                "choice": answer["choice"],
                "confidence": answer["confidence"],
                "usage": result.get("usage", {}),
                "latency_ms": latency,
            })
        finalists, rules = {heat["choice"]: candidates[heat["choice"]] for heat in heats}, FINAL_ACTION
        state = {**state, "all_observed_labels": [record["label"] for record in candidates.values()]}
    choices = {**finalists, **TERMINAL_CHOICES}
    body, result, answer, latency = _systemone_choice(state, choices, rules, request.goal, **call)
    outcome = {
        "choice": answer["choice"],
        "confidence": answer["confidence"],
        "probabilities": answer["probabilities"],
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": _sum_usage([*(heat["usage"] for heat in heats), result.get("usage", {})]),
        "latency_ms": latency + sum(heat["latency_ms"] for heat in heats),
        "request": body,
    }
    if heats:
        outcome["heats"] = heats
    return outcome


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context, *, attempts=None, call_id=None, attempt_limit=None):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = (os.environ.get("TEXT_MODEL_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL") or "deepseek-chat"
    if "api.openai.com/" in base:
        generation = {"max_completion_tokens": 64, "reasoning_effort": "none"}
    elif "api.deepseek.com/" in base:
        generation = {"max_tokens": 1024, "thinking": {"type": "disabled"}}
    else:
        generation = {"max_tokens": 1024, "reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none" and "api.openai.com/" not in base:
        generation = {"max_tokens": 1024, "reasoning": {"enabled": False}}
    if "api.openai.com/" not in base:
        # At the default temperature deepseek-chat returned {"text": null} for 5/58 goals that state
        # the value outright, and 0/57 at 0 (BUG-0029). OpenAI reasoning models reject temperature.
        generation["temperature"] = 0
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "response_format": {"type": "json_object"},
            **generation,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
        audit=attempts,
        call_id=call_id,
        provider="text_helper",
        attempt_limit=attempt_limit,
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
