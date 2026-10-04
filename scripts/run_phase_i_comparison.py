"""Phase I comparison: drive each case through the outer agent and grade it with the external oracle.

Runs on Windows-native Python (the oracles read Win32 state); the outer agent runs in WSL,
started through a generated bash script so nothing crosses the ``wsl.exe`` argv hop (ADR-0015
decision 2). Each run appends one record to ``records.jsonl`` and keeps the raw stream beside it.

The batch stops at the first run that ``halt_reason`` flags or whose setup missed the initial_state:
continuing would score harness faults as system failures or resume from an environment that a
previous delivery left uncertain (plan stop condition 4).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "docs" / "evaluations" / "windows-mcp-replacement" / "case-manifest.json"


def wait_idle(seconds: float, *, timeout_s: float) -> float:
    from finitact.windows_interaction_lease import WindowsIdleTimePrecondition

    idle = WindowsIdleTimePrecondition(minimum_idle_seconds=seconds)
    deadline = time.monotonic() + timeout_s
    while (current := idle.seconds_idle()) < seconds:
        if time.monotonic() > deadline:
            raise SystemExit(f"input never stayed idle for {seconds:.0f}s within {timeout_s:.0f}s")
        time.sleep(1.0)
    return current


def ledger_record(run_id: str) -> dict | None:
    """The server's stored result: stage_ms and provider_usage_* are kept only there (the MCP output omits them)."""
    # Same path as finitact.mcp_server; the server runs as Windows-native Python under this user.
    ledger = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "finitact" / "windows-runs.sqlite3"
    if not ledger.exists():
        return None
    with sqlite3.connect(ledger) as database:
        row = database.execute("SELECT result FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def main() -> None:
    if os.name != "nt":
        raise SystemExit("this runner requires Windows-native Python")
    # A piped Windows stdout defaults to cp932, which cannot print agent answers (e.g. an em dash).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    ctypes.WinDLL("user32").SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # BUG-0017
    from finitact import outer_agent, outer_codex
    from finitact.evaluation_runner import evaluate_run
    from finitact.windows_evaluation_oracles import Win32Probe
    from finitact.windows_screen_grounded import minimum_idle_seconds
    from phase_i_cases import CASE_IDS, prepare

    parser = argparse.ArgumentParser()
    parser.add_argument("system", choices=outer_agent.SYSTEMS)
    parser.add_argument("--case", action="append", choices=CASE_IDS, help="repeatable; default all Phase I cases")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--outer", choices=("claude", "codex"), default="claude")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-budget-usd", type=float, default=1.0)
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--windows-mcp-url", default="http://127.0.0.1:8000/mcp")
    parser.add_argument("--finitact-wrapper", help="WSL path to the registered Finitact MCP launcher (Codex)")
    # Read by the WSL-side job script, so it must be a WSL path.
    parser.add_argument("--wsl-env-file", default=outer_agent.to_wsl_path(str(ROOT / ".env")))
    parser.add_argument("--idle-timeout-s", type=float, default=1800.0)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "phase-i" / time.strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    args.model = args.model or ("sonnet" if args.outer == "claude" else "gpt-6-luna")
    if args.outer == "codex" and args.system == outer_agent.FINITACT and not args.finitact_wrapper:
        parser.error("--finitact-wrapper is required for Codex Finitact runs")
    args.out = args.out.resolve()

    cases = {case["id"]: case for case in json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]}
    args.out.mkdir(parents=True, exist_ok=True)
    config_path = args.out / f"mcp-{args.system}.json"
    config_path.write_text(
        json.dumps(
            outer_agent.mcp_config(
                args.system,
                finitact_python=outer_agent.to_wsl_path(sys.executable),
                windows_mcp_url=args.windows_mcp_url,
            )
        ),
        encoding="utf-8",
    )
    system_prompt_path = args.out / "system-prompt.txt"
    system_prompt_path.write_text(outer_agent.SYSTEM_PROMPT, encoding="utf-8")
    if args.outer == "codex":
        command = outer_codex.build_command(
            codex=["codex"], model=args.model, system=args.system,
            system_prompt=outer_agent.SYSTEM_PROMPT, windows_mcp_url=args.windows_mcp_url,
            finitact_command=args.finitact_wrapper or "",
        )
    else:
        allowed, disallowed = outer_agent.tool_policy(args.system)
        command = outer_agent.build_command(
            claude=["claude"],
            model=args.model,
            mcp_config_path=outer_agent.to_wsl_path(str(config_path)),
            system_prompt_path=outer_agent.to_wsl_path(str(system_prompt_path)),
            allowed=allowed,
            disallowed=disallowed,
            max_budget_usd=args.max_budget_usd,
        )
    script_path = args.out / "outer-agent.sh"
    with open(script_path, "w", encoding="utf-8", newline="\n") as script:
        job = outer_agent.wsl_job_script(
            command,
            cwd="/tmp" if args.outer == "codex" else outer_agent.to_wsl_path(str(ROOT)),
            env_file=args.wsl_env_file,
            env_names=outer_agent.FINITACT_SERVER_ENV,
        )
        # Keep the outer Codex on ChatGPT auth even if a parent shell has API credentials.
        if args.outer == "codex":
            job = job.replace("set -eu\n", "set -eu\nunset OPENAI_API_KEY CODEX_API_KEY\n", 1)
        script.write(job)
    wsl_command = ["wsl.exe", "-e", "bash", outer_agent.to_wsl_path(str(script_path))]

    probe = Win32Probe()
    records_path = args.out / "records.jsonl"
    for case_id in args.case or CASE_IDS:
        case = cases[case_id]
        for trial in range(1, args.trials + 1):
            run_id = f"phase-i-{args.system}-{case_id}-{trial}-{int(time.time())}"
            if args.outer == "codex":
                run_id = f"phase-i-codex-{args.system}-{case_id}-{trial}-{int(time.time())}"
            with prepare(case_id, probe) as prepared:
                idle_s = None
                if case["group"].startswith("screen"):
                    # The lease refuses delivery below minimum_idle_seconds(); both systems get the same wait.
                    idle_s = wait_idle(minimum_idle_seconds() + 2, timeout_s=args.idle_timeout_s)
                target = outer_agent.Target(
                    prepared.target.hwnd, prepared.target.process_id, probe.title(prepared.target.hwnd)
                )
                prompt = outer_agent.build_prompt(args.system, case, target)
                stream_path = args.out / f"{run_id}.stream.jsonl"
                record = evaluate_run(
                    case_id=case_id,
                    run_id=run_id,
                    target=prepared.target_record,
                    oracle=prepared.oracle,
                    drive=lambda: outer_agent.run_outer_agent_timed(
                        wsl_command, prompt, timeout_s=args.timeout_s, stream_path=str(stream_path),
                        summarize=outer_codex.summarize_stream,
                    ) if args.outer == "codex" else outer_agent.run_outer_agent(
                        wsl_command, prompt, timeout_s=args.timeout_s, stream_path=str(stream_path)
                    ),
                    settle_s=prepared.settle_s,
                )
            record.update(system=args.system, outer=args.outer, model=args.model, trial=trial, prompt=prompt,
                          idle_before_s=idle_s)
            summary = record.get("system_result")
            if summary:
                runs = summary.get("finitact_runs") or ()
                stored = {run["run_id"]: ledger_record(run["run_id"]) for run in runs if run.get("run_id")}
                summary["finitact_stage_ms"] = {
                    run_id: [goal.get("stage_ms", {}) for goal in record["goals"]]
                    for run_id, record in stored.items()
                    if record
                }
                summary["finitact_provider_tokens"] = outer_agent._sum_provider_usage(runs, stored.get)
            if not record["external"]["driven"]:
                halt = f"not driven: {record['external']['detail']}"
            elif summary is None:
                halt = f"driver error: {record['drive_error']}"
            else:
                halt = outer_agent.halt_reason(args.system, summary)
                if limit := outer_agent.usage_limit(summary):
                    record.update(valid=False, excluded=limit)
                elif unreachable := outer_agent.provider_unreachable(summary):
                    record.update(valid=False, excluded=unreachable)
                if args.outer == "codex" and not limit:
                    invalid = None
                    if summary.get("non_mcp_items"):
                        invalid = "Codex used a non-MCP tool"
                    elif outer_codex.transport_closed(summary):
                        invalid = "Codex MCP transport closed"
                    elif not summary.get("completed"):
                        invalid = "Codex turn did not complete"
                    if invalid:
                        halt = invalid
                        record.update(valid=False, excluded=invalid)
            record["harness_halt"] = halt
            with open(records_path, "a", encoding="utf-8") as records:
                records.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            outer = summary or {}
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "verdict": record["external"]["verdict"],
                        "answer": outer.get("answer"),
                        "ui_tool_calls": outer.get("ui_tool_calls"),
                        "outer_tokens": outer.get("outer_tokens"),
                        "finitact_provider_tokens": outer.get("finitact_provider_tokens"),
                        "wall_ms": outer.get("wall_ms"),
                        "halt": halt,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if halt:
                raise SystemExit(f"batch stopped: {halt} (records: {records_path})")


if __name__ == "__main__":
    main()
