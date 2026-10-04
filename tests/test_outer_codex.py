import json

from finitact import outer_codex
from finitact.outer_trace import breakdown


def _line(t, event):
    return json.dumps({"t_ms": t, "event": event})


def _call(t, kind, call_id, tool, *, text=None, status="completed"):
    item = {"id": call_id, "type": "mcp_tool_call", "server": "finitact", "tool": tool, "arguments": {"goal": "x"}}
    if kind == "item.started":
        item.update(status="in_progress", result=None, error=None)
    else:
        item.update(status=status, error=None, result={"content": [{"type": "text", "text": text}]})
    return _line(t, {"type": kind, "item": item})


RUN = json.dumps({"run_id": "r1", "goals": [{"metrics": {"provider_input_tokens": 7}}]})
STREAM = [
    _line(0, {"type": "thread.started", "thread_id": "t"}),
    _line(5, {"type": "turn.started"}),
    _call(1000, "item.started", "item_0", "observe_browser"),
    _call(1400, "item.completed", "item_0", "observe_browser", text='{"elements": []}'),
    _call(3000, "item.started", "item_1", "run_browser"),
    _call(9000, "item.completed", "item_1", "run_browser", text=RUN),
    _line(9900, {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "DONE: ok"}}),
    _line(9950, {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 9, "reasoning_output_tokens": 3}}),
]


def test_summary_keeps_codex_usage_names_and_finds_finitact_runs():
    summary = outer_codex.summarize_stream(STREAM)
    assert summary["completed"] and summary["answer"] == "DONE: ok"
    assert summary["outer_tokens"] == {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 9, "reasoning_output_tokens": 3}
    assert [c["name"] for c in summary["tool_calls"]] == ["mcp__finitact__observe_browser", "mcp__finitact__run_browser"]
    assert summary["ui_tool_calls"] == 1
    assert summary["finitact_provider_tokens"] == {"provider_input_tokens": 7}
    assert summary["mcp_servers"] == {"finitact": "connected"} and summary["non_mcp_items"] == []
    assert outer_codex.total_tokens(summary["outer_tokens"]) == 109


def test_steps_split_model_and_tool_time_including_the_closing_answer():
    records = outer_codex.steps(STREAM, wall_ms=10000)
    assert [r["wait_ms"] for r in records] == [1000, 1600, 1000]
    assert [c["tool_ms"] for r in records for c in r["tools"]] == [400, 6000]
    totals = breakdown(records, lambda run_id: {"goals": [{"stage_ms": {"decide": 5}}]} if run_id == "r1" else None)
    assert totals["model_ms"] + totals["tool_ms"] == 10000
    assert totals["finitact_stage_ms"] == {"decide": 5}


def test_usage_limit_and_non_mcp_items_are_reported_for_exclusion():
    limit = outer_codex.summarize_stream([
        _line(0, {"type": "error", "message": "You've hit your usage limit. Try again later."}),
        _line(1, {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}),
    ])
    assert limit["subtype"] == "usage_limit" and not limit["completed"]
    shell = outer_codex.summarize_stream([_line(0, {"type": "item.completed", "item": {"type": "command_execution"}})])
    assert shell["non_mcp_items"] == ["command_execution"]
    assert outer_codex.transport_closed({"tool_calls": [
        {"is_error": True, "result": {"message": "Transport closed"}},
    ]})


def test_command_registers_only_the_compared_server_and_disables_other_channels():
    command = outer_codex.build_command(
        codex=["codex"], model="gpt-6-luna", system="windows-mcp", system_prompt="p",
        windows_mcp_url="http://127.0.0.1:8000/mcp", finitact_command="/x/finitact-mcp-windows.sh",
    )  # fmt: skip
    joined = " ".join(command)
    assert "--ignore-user-config" in command and command[-1] == "-"
    assert "mcp_servers.windows-mcp.url" in joined and "mcp_servers.finitact" not in joined
    assert "PowerShell" in joined
    for feature in ("shell_tool", "computer_use", "browser_use"):
        assert command[command.index(feature) - 1] == "--disable"


def test_finitact_command_fixes_the_benchmark_route_opt_in():
    command = outer_codex.build_command(
        codex=["codex"], model="gpt-6-luna", system="finitact", system_prompt="p",
        windows_mcp_url="unused", finitact_command="/x/finitact-mcp-windows.sh",
    )  # fmt: skip
    assert 'mcp_servers.finitact.env.FINITACT_WINDOW_BROWSER_ROUTE="1"' in command
