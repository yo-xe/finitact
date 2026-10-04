import json

from finitact import outer_agent
from finitact.outer_agent import (
    FINITACT,
    WINDOWS_MCP,
    Target,
    build_command,
    build_prompt,
    halt_reason,
    mcp_config,
    provider_unreachable,
    summarize_stream,
    to_wsl_path,
    tool_policy,
    usage_limit,
    wsl_job_script,
)

CASE = {
    "goal": "Open the dropdown and select Full HD.",
    "group": "screen_two_action",
    "allowed_operations": ["click"],
    "deadline_ms": 45000,
    "action_budget": 2,
    "provider_attempt_budget": 6,
}
TARGET = Target(hwnd=1234, pid=99, title="sample-lab - Unity")


def test_both_systems_get_the_same_goal_and_window_title_only():
    finitact = build_prompt(FINITACT, CASE, TARGET)

    assert finitact == build_prompt(WINDOWS_MCP, CASE, TARGET)
    assert finitact == "Goal: Open the dropdown and select Full HD.\nTarget window: title 'sample-lab - Unity'."


def test_finitact_server_receives_keys_only_by_reference():
    server = mcp_config(FINITACT, finitact_python="python.exe", windows_mcp_url="unused")["mcpServers"][FINITACT]

    assert server["env"]["TYPESAFE_API_KEY"] == "${TYPESAFE_API_KEY:-}"
    assert "TYPESAFE_API_KEY" in server["env"]["WSLENV"].split(":")
    assert server["env"]["FINITACT_WINDOW_BROWSER_ROUTE"] == "1"
    assert "FINITACT_WINDOW_BROWSER_ROUTE" in server["env"]["WSLENV"].split(":")


def test_windows_mcp_non_ui_channels_are_disallowed():
    allowed, disallowed = tool_policy(WINDOWS_MCP)
    command = build_command(
        claude=["claude"],
        model="sonnet",
        mcp_config_path="c.json",
        system_prompt_path="s.txt",
        allowed=allowed,
        disallowed=disallowed,
        max_budget_usd=1.0,
    )

    assert command[command.index("--allowedTools") + 1] == "mcp__windows-mcp"
    assert "mcp__windows-mcp__PowerShell" in command[command.index("--disallowedTools") + 1].split(",")
    assert command[command.index("--setting-sources") + 1] == ""
    assert command[command.index("--tools") + 1] == ""


def test_finitact_policy_has_no_disallowed_argument():
    allowed, disallowed = tool_policy(FINITACT)
    command = build_command(
        claude=["claude"], model="m", mcp_config_path="c", system_prompt_path="s",
        allowed=allowed, disallowed=disallowed, max_budget_usd=1,
    )  # fmt: skip

    assert "--disallowedTools" not in command


def _line(event):
    return json.dumps(event)


def test_stream_summary_pairs_tool_results_and_sums_finitact_provider_tokens():
    run_record = {
        "run_id": "r1",
        "goals": [{"metrics": {"provider_attempts": 2, "provider_usage_prompt_tokens": 300, "mutation_attempts": 2}}],
    }
    lines = [
        _line(
            {
                "type": "system",
                "subtype": "init",
                "model": "claude-sonnet-5",
                "mcp_servers": [{"name": "finitact", "status": "connected"}],
            }
        ),
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "mcp__finitact__run_windows", "input": {}}]
                },
            }
        ),
        _line(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": [{"type": "text", "text": json.dumps(run_record)}],
                        }
                    ]
                },
            }
        ),
        "not json",
        _line(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "DONE",
                "num_turns": 2,
                "total_cost_usd": 0.05,
                "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 500},
            }
        ),
    ]

    summary = summarize_stream(lines)

    assert summary["completed"] is True
    assert summary["outer_tokens"] == {
        "input_tokens": 10,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 500,
        "output_tokens": 20,
    }
    assert summary["finitact_runs"] == [run_record]
    assert summary["finitact_provider_tokens"] == {"provider_attempts": 2, "provider_usage_prompt_tokens": 300}
    assert summary["ui_tool_calls"] == 1
    assert summary["unparsed_lines"] == 1
    assert summary["model"] == "claude-sonnet-5"
    assert summary["mcp_servers"] == {"finitact": "connected"}


def test_stream_without_result_event_is_not_completed_and_read_only_calls_are_not_ui_actions():
    lines = [
        _line(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "a", "name": "mcp__windows-mcp__Snapshot", "input": {}},
                        {"type": "tool_use", "id": "b", "name": "mcp__windows-mcp__Click", "input": {"loc": [1, 2]}},
                    ]
                },
            }
        )
    ]

    summary = summarize_stream(lines)

    assert summary["completed"] is False
    assert summary["is_error"] is None
    assert summary["ui_tool_calls"] == 1
    assert [call["result"] for call in summary["tool_calls"]] == [None, None]


def _goal(**fields):
    return {"finitact_runs": [{"run_id": "r", "goals": [fields]}], "mcp_servers": {FINITACT: "connected"}}


def test_halt_on_harness_fault_or_unsafe_finitact_delivery():
    assert halt_reason(WINDOWS_MCP, {"mcp_servers": {WINDOWS_MCP: "failed"}}) == "windows-mcp MCP server status: failed"
    assert halt_reason(FINITACT, {"timed_out": True}) == "outer agent timed out"
    assert halt_reason(FINITACT, {"answer": "You've hit your session limit · resets 3:10am"}) == (
        "outer agent session limit"
    )
    assert "uncertain" in halt_reason(FINITACT, _goal(mutation_state="uncertain", termination_reason="error"))
    assert "blocked" in halt_reason(FINITACT, _goal(mutation_state="none", termination_reason="blocked"))
    assert halt_reason(FINITACT, _goal(mutation_state="confirmed", termination_reason="provider_done")) is None


def test_weekly_limit_is_excluded_despite_success_subtype():
    summary = summarize_stream([_line({
        "type": "result", "subtype": "success", "is_error": True,
        "result": "You've hit your weekly limit · resets 3pm (Asia/Tokyo)",
    })])
    assert usage_limit(summary) == "outer agent weekly limit"
    assert halt_reason(FINITACT, summary) == "outer agent weekly limit"
    assert usage_limit({**summary, "is_error": False}) is None


def test_provider_connection_failure_is_excluded_but_other_errors_are_not():
    detail = "RuntimeError: Model connection failed; no action executed."
    assert provider_unreachable(_goal(termination_reason="error", detail=detail)) == "Finitact provider connection failed"
    invalid = "ValueError: Invalid TypeSafe response; no action executed."
    assert provider_unreachable(_goal(termination_reason="error", detail=invalid)) is None
    assert provider_unreachable(_goal(termination_reason="blocked", detail=detail)) is None


def test_windows_paths_map_under_mnt():
    assert to_wsl_path(r"C:\Users\me\venv\python.exe") == "/mnt/c/Users/me/venv/python.exe"


def test_job_script_exports_only_named_keys_and_quotes_arguments(tmp_path):
    import subprocess

    env_file = tmp_path / ".env"
    env_file.write_text("# c\nTYPESAFE_API_KEY=k=1\nANTHROPIC_API_KEY=secret\nTEXT_MODEL=m", encoding="utf-8")
    script = tmp_path / "job.sh"
    script.write_text(
        wsl_job_script(
            ["sh", "-c", 'echo "$TYPESAFE_API_KEY|${ANTHROPIC_API_KEY-unset}|$TEXT_MODEL|$1"', "_", ""],
            cwd=str(tmp_path),
            env_file=str(env_file),
            env_names=("TYPESAFE_API_KEY", "TEXT_MODEL"),
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    )

    assert completed.stdout == "k=1|unset|m|\n"


def test_provider_usage_is_counted_from_the_ledger_when_the_result_omits_it():
    runs = [{"run_id": "r1", "goals": [{"metrics": {"provider_attempts": 1}}]}]
    stored = {"r1": {"goals": [{"metrics": {"provider_attempts": 1, "provider_usage_prompt_tokens": 300}}]}}
    assert outer_agent._sum_provider_usage(runs) == {"provider_attempts": 1}
    assert outer_agent._sum_provider_usage(runs, stored.get) == {"provider_attempts": 1, "provider_usage_prompt_tokens": 300}


def test_stream_records_tool_names_and_counts_each_turn_once():
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 4}
    block = {"type": "text", "text": "x"}
    lines = [
        _line({"type": "system", "subtype": "init", "tools": ["b", "a"], "model": "m"}),
        _line({"type": "assistant", "message": {"id": "t1", "content": [block], "usage": usage}}),
        _line({"type": "assistant", "message": {"id": "t1", "content": [block], "usage": usage}}),
        _line({"type": "assistant", "message": {"id": "t2", "content": [block], "usage": {**usage, "cache_read_input_tokens": 100}}}),
    ]

    summary = summarize_stream(lines)

    assert summary["tool_names"] == ["a", "b"]
    assert len(summary["turn_usage"]) == 2
    assert summary["prefix_tokens"] == 102
