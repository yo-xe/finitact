# Evaluation report

Measured on one Windows 11 desktop (Ryzen 7 7700, 24 GiB RAM, RTX 5070 12 GiB): the Claude Sonnet cohort on
2026-10-03 at one Finitact commit, the Codex Luna cohort on 2026-10-01.

- [benchmark.md](benchmark.md): Finitact versus [windows-mcp](https://github.com/CursorTouch/Windows-MCP) on 9 short
  Windows tasks (N=10 per system, separate Claude Sonnet and Codex Luna cohorts) and 5 multi-step tasks
  (N=5 per system, Claude Sonnet only).
- [jev.md](jev.md): decision quality and latency of TypeSafe Jev against two local models on a fixed set of 13
  decision cases, and what that means for how Finitact uses Jev.
- `data/*.jsonl`: one line per trial or decision, the numeric fields only. `c1-windows.jsonl` holds Claude C1 and
  `c1-codex-luna.jsonl` holds Codex C1. The current `figures/*.svg` show the Claude cohort and are rendered by
  `scripts/make_report_figures.py` (standard library only).

Design records in [docs/adr/](../adr/) are in Japanese; the report links them where a number depends on a decision.
