"""Per-turn breakdown of an outer agent's timed stream (docs/evaluations/e2e-live/instrumentation.md).

Input lines are ``{"t_ms": ..., "event": ...}`` written by ``outer_agent.run_outer_agent_timed`` from
``claude -p --output-format stream-json --verbose --include-partial-messages``.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping

MODEL_PARTS = ("wait_ms", "ttft_ms", "thinking_ms", "text_ms", "tool_input_ms")


def turns(lines: Iterable[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    turn: dict[str, Any] | None = None
    blocks: dict[int, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    last_trigger_t, trigger = 0, "prompt"
    for line in lines:
        if not line.strip():
            continue
        wrapped = json.loads(line)
        t, event = wrapped.get("t_ms", 0), wrapped.get("event") or {}
        if event.get("type") == "stream_event":
            inner = event.get("event") or {}
            kind = inner.get("type")
            if kind == "message_start":
                usage = (inner.get("message") or {}).get("usage") or {}
                turn = {
                    "index": len(records) + 1,
                    "trigger": trigger,
                    "t_start": t,
                    "wait_ms": t - last_trigger_t,
                    "ttft_ms": None,
                    "thinking_ms": 0,
                    "text_ms": 0,
                    "tool_input_ms": 0,
                    "text_chars": 0,
                    "tool_input_chars": 0,
                    "context_tokens": sum(
                        usage.get(key, 0)
                        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
                    ),
                    "tools": [],
                }
                records.append(turn)
                blocks = {}
            elif turn is None:
                continue
            elif kind == "content_block_start":
                block = inner.get("content_block") or {}
                blocks[inner.get("index", 0)] = {"type": block.get("type"), "t": t, "name": block.get("name"), "id": block.get("id")}
            elif kind == "content_block_delta":
                if turn["ttft_ms"] is None:
                    turn["ttft_ms"] = t - turn["t_start"]
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta":
                    turn["text_chars"] += len(delta.get("text", ""))
                elif delta.get("type") == "input_json_delta":
                    turn["tool_input_chars"] += len(delta.get("partial_json", ""))
            elif kind == "content_block_stop":
                block = blocks.get(inner.get("index", 0))
                if block is None:
                    continue
                duration = t - block["t"]
                if block["type"] == "thinking":
                    turn["thinking_ms"] += duration
                elif block["type"] == "text":
                    turn["text_ms"] += duration
                elif block["type"] == "tool_use":
                    turn["tool_input_ms"] += duration
                    call = {"name": str(block["name"]).rsplit("__", 1)[-1], "stop_t": t}
                    turn["tools"].append(call)
                    pending[block["id"]] = call
            elif kind == "message_delta":
                usage = inner.get("usage") or {}
                turn["output_tokens"] = usage.get("output_tokens", 0)
                turn["thinking_tokens"] = (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)
            elif kind == "message_stop":
                turn["t_end"] = t
                turn["ttft_ms"] = turn["ttft_ms"] or 0
                # Whole generation, including gaps between blocks the parts above do not cover.
                turn["gen_ms"] = t - turn["t_start"]
        elif event.get("type") == "user":
            for part in (event.get("message") or {}).get("content") or ():
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                call = pending.pop(part.get("tool_use_id"), None)
                if call is None:
                    continue
                content = part.get("content")
                text = "".join(c.get("text", "") for c in content if isinstance(c, dict)) if isinstance(content, list) else str(content or "")
                call.update(tool_ms=t - call.pop("stop_t"), result_chars=len(text), result=_json_or_none(text))
                last_trigger_t, trigger = t, call["name"]
    return records


def breakdown(records: list[dict[str, Any]], ledger: Callable[[str], Mapping | None] | None = None) -> dict[str, Any]:
    """Totals over one trial; ``ledger(run_id)`` returns a stored Finitact run record with stage_ms, if any."""

    totals: dict[str, Any] = {part: sum(r.get(part) or 0 for r in records) for part in (*MODEL_PARTS, "gen_ms")}
    totals["model_ms"] = totals["wait_ms"] + totals["gen_ms"]
    calls = [call for r in records for call in r["tools"]]
    totals["tool_ms"] = sum(call.get("tool_ms", 0) for call in calls)
    totals["turns"] = len(records)
    totals["output_tokens"] = sum(r.get("output_tokens", 0) for r in records)
    totals["thinking_tokens"] = sum(r.get("thinking_tokens", 0) for r in records)
    totals["text_chars"] = sum(r["text_chars"] for r in records)
    totals["tool_input_chars"] = sum(r["tool_input_chars"] for r in records)
    totals["context_tokens_last"] = records[-1]["context_tokens"] if records else 0
    by_tool: dict[str, dict[str, int]] = {}
    stages: dict[str, int] = {}
    for call in calls:
        entry = by_tool.setdefault(call["name"], {"calls": 0, "tool_ms": 0, "result_chars": 0})
        entry["calls"] += 1
        entry["tool_ms"] += call.get("tool_ms", 0)
        entry["result_chars"] += call.get("result_chars", 0)
        run_id = (call.get("result") or {}).get("run_id") if isinstance(call.get("result"), dict) else None
        stored = ledger(run_id) if ledger and run_id else None
        for goal in (stored or {}).get("goals", ()):
            for stage, value in (goal.get("stage_ms") or {}).items():
                if stage in ("observe", "decide", "fresh", "act", "verify", "text"):
                    stages[stage] = stages.get(stage, 0) + value
    totals["by_tool"] = by_tool
    totals["finitact_stage_ms"] = stages
    return totals


def _json_or_none(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None
