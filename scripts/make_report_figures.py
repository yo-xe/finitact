"""Render docs/report/figures/*.svg from docs/report/data/*.jsonl.

Usage: python scripts/make_report_figures.py

Standard library only, so the public repo can regenerate the figures without extra dependencies.
Each SVG carries its own light/dark tokens (prefers-color-scheme) because GitHub embeds it as an image.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "report"

CASE_LABELS = {
    "uia-notepad-fill-001": "Notepad: fill text",
    "uia-calculator-digit-001": "Calculator: press 7",
    "screen-vscode-fill-save-001": "VS Code: replace text, save",
    "screen-tk-scroll-select-001": "Tk: scroll list, select row",
    "screen-tk-drag-drop-001": "Tk: drag card to zone",
    "screen-unity-open-dropdown-001": "Unity: open dropdown",
    "screen-unity-select-resolution-001": "Unity: select resolution",
    "screen-blender-workspace-001": "Blender: switch workspace",
    "screen-blender-select-mode-001": "Blender: select mode",
    "E2E-01": "E2E-01 launch chat app, post",
    "E2E-02": "E2E-02 AWS estimate to CSV",
    "E2E-03": "E2E-03 pick saved email",
    "E2E-04": "E2E-04 taskbar, search, scroll",
    "E2E-05": "E2E-05 new folder, drag files",
}
SYSTEMS = ("finitact", "windows-mcp")
SYSTEM_LABELS = {"finitact": "Finitact", "windows-mcp": "windows-mcp"}
PROVIDER_LABELS = {"typesafe": "TypeSafe Jev", "qwen": "Qwen2.5 14B (local)", "laya": "Laya 0.3.21 (local)"}

STYLE = """
<style>
  svg { --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7;
        --s1:#2a78d6; --s2:#eb6834; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  @media (prefers-color-scheme: dark) {
    svg { --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7; --grid:#2c2c2a; --axis:#383835; --s1:#3987e5; --s2:#d95926; }
  }
  .bg { fill: var(--surface); } .title { fill: var(--ink); font-size: 15px; font-weight: 600; }
  .sub { fill: var(--ink2); font-size: 12px; } .label { fill: var(--ink2); font-size: 12px; }
  .value { fill: var(--ink2); font-size: 11px; font-variant-numeric: tabular-nums; }
  .tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
  .grid { stroke: var(--grid); stroke-width: 1; } .axis { stroke: var(--axis); stroke-width: 1; }
  .ref { stroke: var(--ink2); stroke-width: 1; stroke-dasharray: 4 3; }
  .s1 { fill: var(--s1); } .s2 { fill: var(--s2); } .stem { stroke: var(--s1); stroke-width: 2; }
</style>
"""

LABEL_W = 210
PLOT_W = 420
RIGHT = 70


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (REPORT / "data" / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def medians(rows: list[dict], field: str) -> dict[tuple[str, str], float]:
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        groups[(r["case"], r["system"])].append(r[field])
    return {k: median(v) for k, v in groups.items()}


def svg(width: int, height: int, body: list[str], title: str, desc: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="t d">\n<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>{STYLE}'
        f'<rect class="bg" width="{width}" height="{height}" rx="8"/>\n' + "\n".join(body) + "\n</svg>\n"
    )


def hbar(x0: float, x1: float, y: float, h: float, cls: str, tip: str) -> str:
    # Rounded data end, square at the baseline.
    r = min(4.0, (x1 - x0) / 2, h / 2)
    d = f"M{x0:.1f},{y:.1f} H{x1 - r:.1f} Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f} V{y + h - r:.1f} Q{x1:.1f},{y + h:.1f} {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z"
    return f'<path class="{cls}" d="{d}"><title>{escape(tip)}</title></path>'


def legend(x: float, y: float) -> list[str]:
    out = []
    for i, system in enumerate(SYSTEMS):
        cx = x + i * 110
        out.append(f'<rect class="s{i + 1}" x="{cx}" y="{y - 9}" width="10" height="10" rx="2"/>')
        out.append(f'<text class="label" x="{cx + 15}" y="{y}">{SYSTEM_LABELS[system]}</text>')
    return out


def grouped_bars(rows: list[dict], cases: list[str], field: str, unit: str, title: str, sub: str, fmt, step: float) -> str:
    med = medians(rows, field)
    success = defaultdict(int)
    count = defaultdict(int)
    for r in rows:
        count[(r["case"], r["system"])] += 1
        success[(r["case"], r["system"])] += r["verdict"] == "success"
    top, bar_h, gap, band = 78, 14, 2, 44
    vmax = max(med.values())
    axis_max = math.ceil(vmax / step) * step
    scale = PLOT_W / axis_max
    height = top + band * len(cases) + 36
    width = LABEL_W + PLOT_W + RIGHT
    body = [f'<text class="title" x="16" y="26">{escape(title)}</text>', f'<text class="sub" x="16" y="44">{escape(sub)}</text>']
    body += legend(16, 64)
    for i in range(int(axis_max / step) + 1):
        x = LABEL_W + i * step * scale
        body.append(f'<line class="grid" x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{height - 30}"/>')
        body.append(f'<text class="tick" x="{x:.1f}" y="{height - 14}" text-anchor="middle">{fmt(i * step)}</text>')
    body.append(f'<line class="axis" x1="{LABEL_W}" y1="{top - 6}" x2="{LABEL_W}" y2="{height - 30}"/>')
    for ci, case in enumerate(cases):
        y0 = top + ci * band
        body.append(f'<text class="label" x="{LABEL_W - 10}" y="{y0 + bar_h + 5}" text-anchor="end">{escape(CASE_LABELS[case])}</text>')
        for si, system in enumerate(SYSTEMS):
            v = med[(case, system)]
            y = y0 + si * (bar_h + gap)
            k = (case, system)
            ok = f"{success[k]}/{count[k]}"
            tip = f"{SYSTEM_LABELS[system]}, {CASE_LABELS[case]}: median {fmt(v)}{unit}, success {ok}"
            body.append(hbar(LABEL_W, LABEL_W + v * scale, y, bar_h, f"s{si + 1}", tip))
            flag = "" if success[k] == count[k] else f"  ({ok})"
            body.append(f'<text class="value" x="{LABEL_W + v * scale + 6:.1f}" y="{y + bar_h - 3}">{fmt(v)}{flag}</text>')
    return svg(width, height, body, title, sub)


def ratio_chart(rows: list[dict], cases: list[str], field: str, title: str, sub: str) -> str:
    med = medians(rows, field)
    lo, hi = 0.5, 8.0
    top, band = 84, 26
    height = top + band * len(cases) + 40
    width = LABEL_W + PLOT_W + RIGHT

    def x_of(v: float) -> float:
        return LABEL_W + (math.log2(v) - math.log2(lo)) / (math.log2(hi) - math.log2(lo)) * PLOT_W

    body = [f'<text class="title" x="16" y="26">{escape(title)}</text>', f'<text class="sub" x="16" y="44">{escape(sub)}</text>']
    for t in (0.5, 1, 2, 4, 8):
        x = x_of(t)
        body.append(f'<line class="{"ref" if t == 1 else "grid"}" x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{height - 34}"/>')
        body.append(f'<text class="tick" x="{x:.1f}" y="{height - 18}" text-anchor="middle">{t:g}×</text>')
    body.append(f'<text class="tick" x="{x_of(1) - 6:.1f}" y="{top - 12}" text-anchor="end">Finitact higher</text>')
    body.append(f'<text class="tick" x="{x_of(1) + 6:.1f}" y="{top - 12}">windows-mcp higher</text>')
    for ci, case in enumerate(cases):
        y = top + ci * band + band / 2
        ratio = med[(case, "windows-mcp")] / med[(case, "finitact")]
        x = x_of(min(max(ratio, lo), hi))
        body.append(f'<text class="label" x="{LABEL_W - 10}" y="{y + 4:.1f}" text-anchor="end">{escape(CASE_LABELS[case])}</text>')
        body.append(f'<line class="stem" x1="{x_of(1):.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{y:.1f}"/>')
        tip = f"{CASE_LABELS[case]}: windows-mcp {med[(case, 'windows-mcp')]:g} / Finitact {med[(case, 'finitact')]:g} = {ratio:.2f}×"
        body.append(f'<circle class="s1" cx="{x:.1f}" cy="{y:.1f}" r="5"><title>{escape(tip)}</title></circle>')
        anchor, dx = ("start", 10) if ratio >= 1 else ("end", -10)
        body.append(f'<text class="value" x="{x + dx:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}">{ratio:.2f}×</text>')
    return svg(width, height, body, title, sub)


def accuracy_chart(rows: list[dict]) -> str:
    providers = ["typesafe", "qwen", "laya"]
    passed = defaultdict(int)
    total = defaultdict(int)
    for r in rows:
        total[r["provider"]] += 1
        passed[r["provider"]] += r["status"] == "pass"
    top, band, bar_h = 64, 34, 18
    height = top + band * len(providers) + 40
    width = LABEL_W + PLOT_W + RIGHT
    title = "Decision-only accuracy by provider"
    sub = "Share of 130 decisions (13 fixtures × 10) matching the expected choice. Dashed: 90% adoption threshold."
    body = [f'<text class="title" x="16" y="26">{escape(title)}</text>', f'<text class="sub" x="16" y="44">{escape(sub)}</text>']
    for pct in range(0, 101, 20):
        x = LABEL_W + pct / 100 * PLOT_W
        body.append(f'<line class="grid" x1="{x:.1f}" y1="{top - 8}" x2="{x:.1f}" y2="{height - 34}"/>')
        body.append(f'<text class="tick" x="{x:.1f}" y="{height - 18}" text-anchor="middle">{pct}%</text>')
    for pi, p in enumerate(providers):
        share = passed[p] / total[p]
        y = top + pi * band + (band - bar_h) / 2
        body.append(f'<text class="label" x="{LABEL_W - 10}" y="{y + bar_h - 4:.1f}" text-anchor="end">{PROVIDER_LABELS[p]}</text>')
        tip = f"{PROVIDER_LABELS[p]}: {passed[p]}/{total[p]} ({share:.1%})"
        body.append(hbar(LABEL_W, LABEL_W + share * PLOT_W, y, bar_h, "s1", tip))
        body.append(f'<text class="value" x="{LABEL_W + share * PLOT_W + 6:.1f}" y="{y + bar_h - 4:.1f}">{share:.1%}</text>')
    x90 = LABEL_W + 0.9 * PLOT_W
    body.append(f'<line class="ref" x1="{x90:.1f}" y1="{top - 8}" x2="{x90:.1f}" y2="{height - 34}"/>')
    body.append(f'<line class="axis" x1="{LABEL_W}" y1="{top - 8}" x2="{LABEL_W}" y2="{height - 34}"/>')
    return svg(width, height, body, title, sub)


def main() -> None:
    c1, c2, c3 = load("c1-windows.jsonl"), load("c2-e2e.jsonl"), load("c3-decision.jsonl")
    c1_cases = [c for c in CASE_LABELS if not c.startswith("E2E")]
    c2_cases = [c for c in CASE_LABELS if c.startswith("E2E")]
    both = c1 + c2
    out = REPORT / "figures"
    out.mkdir(parents=True, exist_ok=True)
    figures = {
        "c1-time.svg": grouped_bars(
            c1, c1_cases, "wall_s", " s", "Short Windows tasks: median time per trial",
            "Seconds from launching the calling agent to its exit, N=10 per bar.", lambda v: f"{v:.1f}" if v % 1 else f"{v:g}", 10,
        ),
        "c1-tokens.svg": grouped_bars(
            c1, c1_cases, "outer_tokens", " tokens", "Short Windows tasks: median calling-agent tokens",
            "Input incl. cache reads + output of the calling agent, N=10 per bar.", lambda v: f"{v / 1000:.1f}k" if v % 1000 else f"{v / 1000:g}k", 20000,
        ),
        "c2-time.svg": grouped_bars(
            c2, c2_cases, "wall_s", " s", "Multi-step tasks: median time per trial",
            "Seconds per trial, N=5 per bar. Success count shown where below 5/5.", lambda v: f"{v:.1f}" if v % 1 else f"{v:g}", 20,
        ),
        "c2-tokens.svg": grouped_bars(
            c2, c2_cases, "outer_tokens", " tokens", "Multi-step tasks: median calling-agent tokens",
            "Input incl. cache reads + output of the calling agent, N=5 per bar.", lambda v: f"{v / 1000:.1f}k" if v % 1000 else f"{v / 1000:g}k", 200000,
        ),
        "ratio-time.svg": ratio_chart(
            both, c1_cases + c2_cases, "wall_s", "Time: windows-mcp median ÷ Finitact median",
            "Right of 1× means Finitact finished sooner. Log scale.",
        ),
        "ratio-tokens.svg": ratio_chart(
            both, c1_cases + c2_cases, "outer_tokens", "Calling-agent tokens: windows-mcp median ÷ Finitact median",
            "Right of 1× means the calling agent used fewer tokens with Finitact. Log scale.",
        ),
        "decision-accuracy.svg": accuracy_chart(c3),
    }
    for name, text in figures.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        print(out / name)


if __name__ == "__main__":
    main()
