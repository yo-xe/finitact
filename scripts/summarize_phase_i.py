"""Summarize Phase I comparison records into the ADR-0015 decision 6 verdict per case and system.

Usage: python scripts/summarize_phase_i.py <run_dir> [--json]
<run_dir> holds one ``<system>/records.jsonl`` per system (``run_phase_i_comparison.py --out``).
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def load(run_dir: Path) -> list[dict]:
    records = []
    for path in sorted(run_dir.glob("*/records.jsonl")):
        with open(path, encoding="utf-8") as f:
            records.extend(json.loads(line) for line in f if line.strip())
    return records


def judge(successes: int, scored: int) -> str:
    if scored == 0:
        return "判定不能"
    if successes == scored:
        return "可"
    return "限定可" if successes else "不可"


def summarize(records: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for record in records:
        groups[(record.get("outer", "claude"), record["case_id"], record["system"])].append(record)
    rows = []
    for (agent, case_id, system), runs in sorted(groups.items()):
        # ADR-0015 decision 6: runs without external evidence leave the denominator.
        valid_runs = [r for r in runs if r.get("valid", True)]
        scored = [r for r in valid_runs if r["external"].get("driven")
                  and r["external"]["verdict"] in {"success", "failure"}]
        successes = sum(r["external"]["verdict"] == "success" for r in scored)
        # Invalid runs (for example, a blank GUI or a usage-limit stop) do not contribute
        # to timings, usage, or cost either. Valid but externally undetermined runs do.
        results = [r.get("system_result") or {} for r in valid_runs]
        if agent == "codex":
            # Cached input and reasoning output are already included in Codex's input and output.
            outer = [sum((s.get("outer_tokens") or {}).get(k, 0) for k in ("input_tokens", "output_tokens"))
                     for s in results]
        else:
            outer = [sum((s.get("outer_tokens") or {}).get(k, 0)
                         for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
                     for s in results]
        # Screen runs report only input/output usage, UIA runs also a total.
        provider = [
            sum((s.get("finitact_provider_tokens") or {}).get(k, 0) for k in ("provider_usage_input_tokens", "provider_usage_output_tokens"))
            for s in results
        ]
        rows.append(
            {
                "case_id": case_id,
                "system": system,
                "outer": agent,
                "runs": len(runs),
                "scored": len(scored),
                "unscored": len(runs) - len(scored),
                "successes": successes,
                "verdict": judge(successes, len(scored)),
                "verdicts": [r["external"]["verdict"] for r in runs],
                "median_ui_tool_calls": median((s.get("ui_tool_calls") or 0 for s in results)) if results else None,
                "median_outer_tokens": median(outer) if outer else None,
                "median_provider_tokens": median(provider) if provider else None,
                "median_wall_s": round(median((s.get("wall_ms") or 0) for s in results) / 1000, 1) if results else None,
                "total_cost_usd": round(sum(s.get("total_cost_usd") or 0 for s in results), 4),
                "halts": [r["harness_halt"] for r in runs if r.get("harness_halt")],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = summarize(load(args.run_dir))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print("| outer | case | system | 成功/分母 | 判定 | UI tool中央値 | 外側token中央値 | 内部token中央値 | 時間中央値(s) | 費用USD |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        excluded = f"(除外{r['unscored']})" if r["unscored"] else ""
        print(
            f"| {r['outer']} | {r['case_id']} | {r['system']} | {r['successes']}/{r['scored']}{excluded} | {r['verdict']} "
            f"| {r['median_ui_tool_calls']} | {r['median_outer_tokens']} | {r['median_provider_tokens']} "
            f"| {r['median_wall_s']} | {r['total_cost_usd']} |"
        )
    for r in rows:
        if r["halts"]:
            print(f"halt {r['case_id']} {r['system']}: {r['halts']}")


if __name__ == "__main__":
    main()
