"""Median/min/max over E2E trials with a breakdown (docs/evaluations/e2e-live/instrumentation.md)."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "docs" / "evaluations" / "e2e-live" / "results.jsonl"
FIELDS = (
    "seconds", "outer_tokens", "cost_usd", "breakdown.turns", "breakdown.model_ms", "breakdown.wait_ms",
    "breakdown.ttft_ms", "breakdown.thinking_ms", "breakdown.text_ms", "breakdown.tool_input_ms", "breakdown.gen_ms",
    "breakdown.tool_ms", "breakdown.output_tokens", "breakdown.thinking_tokens", "breakdown.tool_input_chars",
    "breakdown.text_chars", "breakdown.context_tokens_last",
)  # fmt: skip


def value(record: dict, path: str):
    for key in path.split("."):
        record = record.get(key) if isinstance(record, dict) else None
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario")
    parser.add_argument("--since", help="only records whose date is on or after this YYYY-MM-DD")
    parser.add_argument("--label", help="only records whose note contains this label")
    args = parser.parse_args()
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if "breakdown" not in record or record.get("valid") is False:
            continue
        if args.scenario and record["scenario"] != args.scenario:
            continue
        if args.since and record["date"] < args.since:
            continue
        if args.label and args.label not in str(record.get("note", "")):
            continue
        # Codex and Claude count tokens differently (finitact/outer_codex.py); cohorts are never pooled.
        groups.setdefault((record["scenario"], record["tool"], record.get("outer", "claude")), []).append(record)
    for (scenario, tool, outer), records in sorted(groups.items()):
        wins = sum(bool(r["success"]) for r in records)
        print(f"== {scenario} {tool} ({outer}): n={len(records)} success={wins}/{len(records)}")
        for field in FIELDS:
            values = [v for v in (value(r, field) for r in records) if isinstance(v, (int, float))]
            if values:
                print(f"  {field:28s} median={statistics.median(values):>12.1f} min={min(values):>12.1f} max={max(values):>12.1f}")
        stages: dict[str, list[int]] = {}
        for record in records:
            for stage, ms in (value(record, "breakdown.finitact_stage_ms") or {}).items():
                stages.setdefault(stage, []).append(ms)
        for stage, values in sorted(stages.items()):
            print(f"  finitact.{stage:19s} median={statistics.median(values):>12.1f}")


if __name__ == "__main__":
    main()
