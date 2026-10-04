"""Codex CLI (``codex exec --json``) as the outer agent of a separate cohort (docs/evaluations/e2e-live/instrumentation.md).

Codex reports usage under its own definitions (input includes cached input, output includes reasoning), so records
keep Codex's key names and are never pooled with the Claude cohort.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from finitact import outer_agent

USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
# The Claude cohort runs with ``--tools ""``; these give Codex channels around the compared MCP server
# (shell, its own computer/browser use, sub-agents) and would let it reach the goal without the system under test.
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "computer_use", "browser_use", "browser_use_external", "apps", "plugins",
    "multi_agent", "image_generation", "view_image", "memories", "skill_search", "tool_suggest", "goals",
)  # fmt: skip
USAGE_LIMIT_MARKER = "hit your usage limit"


def total_tokens(usage: dict[str, int]) -> int:
    """Codex cached input and reasoning output are subsets of input and output."""
    return (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)


def transport_closed(summary: dict[str, Any]) -> bool:
    """A completed oracle state cannot make a lost MCP result a valid tool trial."""
    return any(
        call.get("is_error") and "transport closed" in str(call.get("result") or "").lower()
        for call in summary.get("tool_calls") or ()
    )


def build_command(
    *,
    codex: Sequence[str],
    model: str,
    system: str,
    system_prompt: str,
    windows_mcp_url: str,
    finitact_command: str,
) -> list[str]:
    command = [
        *codex, "exec", "--json", "--ignore-user-config", "--skip-git-repo-check", "--ephemeral",
        "-m", model,
        # read-only makes Codex ask approval for every non-read-only MCP call, which ``never`` then rejects.
        "-s", "danger-full-access",
        "-c", 'approval_policy="never"',
        "-c", 'web_search="disabled"',
        "-c", f"developer_instructions={json.dumps(system_prompt)}",
    ]  # fmt: skip
    for feature in DISABLED_FEATURES:
        command += ["--disable", feature]
    prefix = f"mcp_servers.{system}"
    if system == outer_agent.FINITACT:
        command += [
            "-c", f"{prefix}.command={json.dumps(finitact_command)}",
            "-c", f"{prefix}.startup_timeout_sec=60",
            # A run_browser call on E2E-02 can take minutes; the default 60 s would cut it off.
            "-c", f"{prefix}.tool_timeout_sec=900",
            "-c", f"{prefix}.enabled_tools={json.dumps(list(outer_agent.FINITACT_TOOLS))}",
            *(arg for name, value in outer_agent.FINITACT_BENCHMARK_ENV.items()
              for arg in ("-c", f"{prefix}.env.{name}={json.dumps(value)}")),
        ]  # fmt: skip
    elif system == outer_agent.WINDOWS_MCP:
        command += [
            "-c", f"{prefix}.url={json.dumps(windows_mcp_url)}",
            "-c", f"{prefix}.tool_timeout_sec=900",
            "-c", f"{prefix}.disabled_tools={json.dumps(list(outer_agent.WINDOWS_MCP_EXCLUDED_TOOLS))}",
        ]  # fmt: skip
    else:
        raise ValueError(f"unknown system: {system}")
    return [*command, "-"]


def _events(lines: Iterable[str]) -> Iterable[tuple[int, dict[str, Any]]]:
    """Accept both raw ``--json`` lines and the ``{"t_ms", "event"}`` lines of ``run_outer_agent_timed``."""

    for line in lines:
        if not line.strip():
            continue
        try:
            wrapped = json.loads(line)
        except ValueError:
            continue
        if "event" in wrapped and "t_ms" in wrapped:
            yield wrapped["t_ms"], wrapped["event"] or {}
        elif "raw" not in wrapped:
            yield 0, wrapped


def summarize_stream(lines: Iterable[str]) -> dict[str, Any]:
    """Same keys as ``outer_agent.summarize_stream`` where Codex has an equivalent; the rest are None."""

    usage = dict.fromkeys(USAGE_KEYS, 0)
    tool_calls: list[dict[str, Any]] = []
    other_items: list[str] = []
    answer, errors, completed, failed, servers = None, [], False, False, {}
    for _t, event in _events(lines):
        kind = event.get("type")
        item = event.get("item") or {}
        if kind == "turn.completed":
            completed = True
            for key in USAGE_KEYS:
                usage[key] += (event.get("usage") or {}).get(key, 0) or 0
        elif kind in ("turn.failed", "error"):
            failed = True
            errors.append(str(event.get("message") or (event.get("error") or {}).get("message") or ""))
        elif kind == "item.completed" and item.get("type") == "agent_message":
            answer = item.get("text")
        elif kind == "item.completed" and item.get("type") == "mcp_tool_call":
            servers[item.get("server")] = "connected" if item.get("status") == "completed" else servers.get(item.get("server"))
            result = item.get("result") or {}
            tool_calls.append(
                {
                    "name": f"mcp__{item.get('server')}__{item.get('tool')}",
                    "input": item.get("arguments"),
                    "is_error": item.get("status") != "completed" or bool(item.get("error")),
                    "result": outer_agent._tool_result_value(result.get("content")) if result else item.get("error"),
                }
            )
        elif kind == "item.completed" and item.get("type") not in ("reasoning", "todo_list"):
            other_items.append(str(item.get("type")))
    finitact_runs = [
        call["result"]
        for call in tool_calls
        if call["name"] in (f"mcp__{outer_agent.FINITACT}__run_windows", f"mcp__{outer_agent.FINITACT}__run_browser")
        and isinstance(call["result"], dict)
    ]
    return {
        "completed": completed and not failed,
        "is_error": failed,
        "subtype": "usage_limit" if any(USAGE_LIMIT_MARKER in e for e in errors) else ("error" if failed else None),
        "answer": answer if answer is not None else ("\n".join(errors) or None),
        "model": None,
        "mcp_servers": servers,
        "num_turns": None,
        "duration_ms": None,
        "total_cost_usd": None,
        "outer_tokens": usage,
        "tool_calls": tool_calls,
        "ui_tool_calls": sum(
            1 for call in tool_calls if call["name"].rsplit("__", 1)[-1] not in outer_agent.READ_ONLY_TOOLS
        ),
        "finitact_runs": finitact_runs,
        "finitact_provider_tokens": outer_agent._sum_provider_usage(finitact_runs),
        # Anything but MCP calls and messages means the run left the compared channel; the trial is invalid.
        "non_mcp_items": other_items,
        "unparsed_lines": 0,
    }


def steps(lines: Iterable[str], *, wall_ms: int | None = None) -> list[dict[str, Any]]:
    """Model steps split at MCP calls, shaped for ``outer_trace.breakdown``.

    ``--json`` has no per-response usage or block timing, so a step's whole gap is ``wait_ms`` and token
    fields are 0; totals come from ``summarize_stream``. With ``wall_ms`` the closing answer after the last
    tool result is added as a final step with no tool.
    """

    records: list[dict[str, Any]] = []
    started: dict[str, int] = {}
    last_t, trigger = 0, "prompt"
    for t, event in _events(lines):
        item = event.get("item") or {}
        if item.get("type") != "mcp_tool_call":
            continue
        if event.get("type") == "item.started":
            call_id = item.get("id")
            if call_id in started:
                continue
            started[call_id] = t
            records.append(
                {
                    "index": len(records) + 1,
                    "trigger": trigger,
                    "wait_ms": max(t - last_t, 0),
                    "ttft_ms": 0, "thinking_ms": 0, "text_ms": 0, "tool_input_ms": 0, "gen_ms": 0,
                    "text_chars": 0,
                    "tool_input_chars": len(json.dumps(item.get("arguments") or {}, ensure_ascii=False)),
                    "context_tokens": 0,
                    "tools": [{"name": str(item.get("tool")), "call_id": call_id}],
                }
            )  # fmt: skip
        elif event.get("type") == "item.completed" and item.get("id") in started:
            text = "".join(
                c.get("text", "") for c in (item.get("result") or {}).get("content") or () if isinstance(c, dict)
            )
            for record in records:
                for call in record["tools"]:
                    if call.get("call_id") == item.get("id"):
                        call.update(
                            tool_ms=t - started[item["id"]],
                            result_chars=len(text),
                            result=outer_agent._tool_result_value(text) if text else None,
                        )
            last_t, trigger = t, str(item.get("tool"))
    if wall_ms is not None:
        records.append(
            {
                "index": len(records) + 1, "trigger": trigger, "wait_ms": max(wall_ms - last_t, 0),
                "ttft_ms": 0, "thinking_ms": 0, "text_ms": 0, "tool_input_ms": 0, "gen_ms": 0,
                "text_chars": 0, "tool_input_chars": 0, "context_tokens": 0, "tools": [],
            }
        )  # fmt: skip
    return records
