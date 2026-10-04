"""Build docs/report/data/*.jsonl from the private C1/C2/C3 run records (ADR-0049).

Usage: python scripts/build_report_data.py --phase-i <Windows-side artifacts/phase-i dir>

Private on purpose: the source records hold window handles, local paths, private target names and answers,
so only the numeric per-trial fields are copied out. The run-set lists below are the canonical C1/C2 sets.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "report" / "data"

# 2026-10-03 single snapshot. A provider halt or the outer 5h limit stopped a batch mid-way, so each system's
# trials span several resumed dirs; trials an outer usage limit cut off are invalid (BUG-0070) and stay out.
C1_DIRS = {
    "finitact": [f"c1-20261003-finitact{s}" for s in ("", "-r2", "-r3", "-r4", "-r5")],
    "windows-mcp": [f"c1-20261003-windows-mcp{s}" for s in ("", "-r2", "-r3")],
}
# The first Blender trials ran beside a manually opened same-name Blender, which breaks the single-window case
# condition; both systems were retaken without it (public-benchmark-20261003.md), and only the retake counts.
C1_RETAKEN = {"screen-blender-select-mode-001", "screen-blender-workspace-001"}
C1_RETAKE_DIRS = {
    "finitact": ["c1-20261003b-finitact", "c1-20261003b-finitact-r2", "c1-20261003b-finitact-r3"],
    "windows-mcp": ["c1-20261003b-windows-mcp"],
}

# E2E-03 finitact and E2E-04 are the -r2 reruns after launcher faults that stopped before any trial.
C2_SETS = {
    (f"E2E-0{n}", system): f"c2-20261003-E2E-0{n}-{system}" for n in range(1, 6) for system in ("finitact", "windows-mcp")
}
for key in (("E2E-03", "finitact"), ("E2E-04", "finitact"), ("E2E-04", "windows-mcp")):
    C2_SETS[key] += "-r2"
# Retaken from an empty desk: the first set started with other windows maximized over it, unlike the control.
C2_SETS[("E2E-01", "finitact")] = "c2-20261003b-E2E-01-finitact"
# Only E2E-02 Finitact was retaken after the window-to-browser route was fixed on (ADR-0052, e2e02-route-20261004.md).
C2_SETS[("E2E-02", "finitact")] = "e2e02-route-20261004d"

OUTER_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
# USD per token. Sonnet 5.5 with the 1h cache write Claude Code uses (its cost_usd reproduces at $4, not $2.50).
OUTER_PRICE = {"input_tokens": 2e-6, "cache_creation_input_tokens": 4e-6, "cache_read_input_tokens": 0.2e-6, "output_tokens": 10e-6}
# Jev bills input only (docs.typesafe.ai/models, 2026-10-04). The text-goal model is deepseek-chat at peak rates,
# an upper bound because off-peak is half (api-docs.deepseek.com pricing, 2026-09-10).
PROVIDER_PRICE = {
    "provider_usage_input_tokens": 0.042e-6,
    "provider_usage_prompt_cache_miss_tokens": 0.30e-6,
    "provider_usage_prompt_cache_hit_tokens": 0.006e-6,
    "provider_usage_completion_tokens": 1.20e-6,
}


def outer_cost(detail: dict) -> float:
    return round(sum(detail.get(k, 0) * v for k, v in OUTER_PRICE.items()), 4)


def provider_cost(usage: dict) -> float:
    return round(sum(usage.get(k, 0) * v for k, v in PROVIDER_PRICE.items()), 4)


def finitact_commits() -> list[tuple[int, str]]:
    log = subprocess.run(
        ["git", "log", "--format=%ct %h", "--", "finitact/"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [(int(t), h) for t, h in (line.split() for line in log.splitlines())]


def code_at(commits: list[tuple[int, str]], epoch: int) -> str:
    # The Windows-side copy is synced from HEAD before a run, so the newest finitact/ commit before the run is the code.
    return next(h for t, h in commits if t <= epoch)


def build_c1(phase_i: Path, commits) -> list[dict]:
    rows = []
    trials: dict[tuple[str, str], int] = defaultdict(int)
    sources = [(system, phase_i / d / "records.jsonl", False) for system, dirs in C1_DIRS.items() for d in dirs]
    sources += [(system, phase_i / d / "records.jsonl", True) for system, dirs in C1_RETAKE_DIRS.items() for d in dirs]
    for system, path, retake in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if "limit" in (r.get("harness_halt") or "") or (r["case_id"] in C1_RETAKEN) != retake:
                continue
            case_id = r["case_id"]
            trials[(case_id, system)] += 1
            s = r.get("system_result") or {}
            provider = s.get("finitact_provider_tokens") or {}
            epoch = int(r["run_id"].rsplit("-", 1)[1])
            rows.append(
                {
                    "suite": "C1",
                    "case": case_id,
                    "system": system,
                    "trial": trials[(case_id, system)],
                    "verdict": r["external"]["verdict"],
                    "wall_s": round((s.get("wall_ms") or 0) / 1000, 1),
                    "outer_tokens": sum((s.get("outer_tokens") or {}).get(k, 0) for k in OUTER_KEYS),
                    "outer_cost_usd": outer_cost(s.get("outer_tokens") or {}),
                    "outer_model": s.get("model"),
                    "outer_turns": s.get("num_turns"),
                    "ui_tool_calls": s.get("ui_tool_calls"),
                    "provider_attempts": provider.get("provider_attempts"),
                    "provider_tokens": provider.get("provider_usage_input_tokens", 0) + provider.get("provider_usage_output_tokens", 0)
                    if system == "finitact"
                    else None,
                    "provider_cost_usd": provider_cost(provider) if system == "finitact" else None,
                    "finitact_commit": code_at(commits, epoch) if system == "finitact" else None,
                }
            )
    return sorted(rows, key=lambda x: (x["case"], x["system"], x["trial"]))


def build_codex_c1(phase_i: Path) -> list[dict]:
    """Export the separate Luna C1 cohort without private paths, window ids, or answers."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for run_dir in sorted(phase_i.glob("codex-luna-c1-2026*")):
        if "smoke" in run_dir.name or not (run_dir / "records.jsonl").exists():
            continue
        for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if not r.get("valid", True):
                continue
            if r.get("outer") != "codex" or r.get("model") != "gpt-6-luna":
                raise ValueError(f"unexpected cohort in {run_dir.name}")
            grouped[(r["case_id"], r["system"])].append(r)

    if len(grouped) != 18 or any(len(runs) != 10 for runs in grouped.values()):
        raise ValueError("Codex C1 export requires 9 cases x 2 systems x 10 valid runs")

    rows = []
    for (case_id, system), runs in sorted(grouped.items()):
        for trial, r in enumerate(sorted(runs, key=lambda x: int(x["run_id"].rsplit("-", 1)[1])), 1):
            s = r.get("system_result") or {}
            provider = s.get("finitact_provider_tokens") or {}
            rows.append(
                {
                    "suite": "C1", "case": case_id, "system": system, "trial": trial,
                    "verdict": r["external"]["verdict"],
                    "wall_s": round((s.get("wall_ms") or 0) / 1000, 3),
                    "outer_tokens": sum((s.get("outer_tokens") or {}).get(k, 0) for k in ("input_tokens", "output_tokens")),
                    "outer_model": "gpt-6-luna", "outer_turns": s.get("num_turns"),
                    "ui_tool_calls": s.get("ui_tool_calls"),
                    "provider_attempts": provider.get("provider_attempts"),
                    "provider_tokens": provider.get("provider_usage_input_tokens", 0) + provider.get("provider_usage_output_tokens", 0)
                    if system == "finitact" else None,
                    "finitact_commit": "83a5095" if system == "finitact" else None,
                }
            )
    return rows


def build_c2(commits) -> list[dict]:
    rows = []
    for line in (ROOT / "docs/evaluations/e2e-live/results.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        set_dir = (r.get("stream") or "").split("/")[-2:-1]
        if not set_dir or C2_SETS.get((r["scenario"], r["tool"])) != set_dir[0]:
            continue
        if r["scenario"] == "E2E-04" and (r.get("oracle") or {}).get("route", {}).get("status") != "pass":
            raise ValueError("E2E-04 export requires independently verified search route (BUG-0071)")
        provider = r.get("finitact_provider_tokens") or {}
        epoch = int(Path(r["stream"]).name.split("-")[-1].split(".")[0])
        rows.append(
            {
                "suite": "C2",
                "case": r["scenario"],
                "system": r["tool"],
                "trial": r["trial"],
                "verdict": "success" if r["success"] else "failure",
                "wall_s": r["seconds"],
                "outer_tokens": r["outer_tokens"],
                "outer_cost_usd": outer_cost(r["outer_tokens_detail"]),
                "outer_model": r["model"],
                "outer_turns": (r.get("breakdown") or {}).get("turns"),
                "ui_tool_calls": r.get("ui_tool_calls"),
                "provider_attempts": provider.get("provider_attempts"),
                "provider_tokens": provider.get("provider_usage_input_tokens", 0) + provider.get("provider_usage_output_tokens", 0)
                if r["tool"] == "finitact"
                else None,
                "provider_cost_usd": provider_cost(provider) if r["tool"] == "finitact" else None,
                "finitact_commit": code_at(commits, epoch) if r["tool"] == "finitact" else None,
            }
        )
    return sorted(rows, key=lambda x: (x["case"], x["system"], x["trial"]))


def build_c3() -> list[dict]:
    rows = []
    src = ROOT / "docs/evaluations/provider-comparison"
    for provider in ("typesafe", "qwen", "laya"):
        d = json.loads((src / f"decision-only-{provider}.json").read_text(encoding="utf-8"))
        for case in d["cases"]:
            for run in case["runs"]:
                rows.append(
                    {
                        "suite": "C3",
                        "provider": provider,
                        "model": d["model"],
                        "case": case["id"],
                        "repetition": run["repetition"],
                        "status": run["status"],
                        "choice": run.get("choice"),
                        "expected": run.get("expected"),
                        "latency_ms": (run.get("measurements") or {}).get("latency_ms"),
                    }
                )
    return rows


def write(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / name, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{name}: {len(rows)} rows")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-i", type=Path)
    parser.add_argument("--codex-phase-i", type=Path, help="export the separate Codex Luna C1 cohort")
    args = parser.parse_args()
    if not args.phase_i and not args.codex_phase_i:
        parser.error("pass --phase-i or --codex-phase-i")
    if args.phase_i:
        commits = finitact_commits()
        write("c1-windows.jsonl", build_c1(args.phase_i, commits))
        write("c2-e2e.jsonl", build_c2(commits))
        write("c3-decision.jsonl", build_c3())
    if args.codex_phase_i:
        write("c1-codex-luna.jsonl", build_codex_c1(args.codex_phase_i))


if __name__ == "__main__":
    main()
