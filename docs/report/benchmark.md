# Finitact versus windows-mcp

The Claude rows were measured on 2026-10-03 against one Finitact commit (`95d2eac`) with the same prompt, preparation,
calling-agent model and budgets for both systems. The one exception is E2E-02 Finitact, retaken alone on 2026-10-04
at commit `f743920` with the window-to-browser route on ([E2E-02 retake](#e2e-02-retake)). The Codex Luna rows are an earlier, separate cohort (2026-10-01,
commit `83a5095`) with a different calling-agent model; keep the two apart when interpreting differences.

## Summary

- Short Windows tasks (C1, Claude): windows-mcp succeeded 10/10 on all 9 tasks; Finitact 10/10 on 8 and 9/10 on the
  Unity resolution task (one Jev connection failure). Finitact was faster on 7 of 9, and the calling agent used
  2.1× to 7.8× fewer tokens with it on all 9.
- Multi-step tasks (C2, Claude): windows-mcp succeeded 5/5 on all 5. Finitact succeeded 5/5 on four and 4/5 on the chat
  task. With Finitact the calling agent used fewer tokens on four (1.3× to 3.2×) and more on one (Wikipedia 1.3×);
  Finitact was faster on three.
- The token gap is widest where windows-mcp needs many observe-act turns (VS Code, Unity resolution, Explorer drag)
  and reverses on the long Wikipedia task, where the calling agent drives Finitact goal by goal. Finitact spends extra
  tokens on Jev that the calling agent does not pay for; they are listed separately below.
- Counting Jev and the calling agent at list prices, a trial cost less with Finitact on all 14 tasks: 1.5× to 6.4×
  on the short tasks and 1.2× to 2.1× on the multi-step ones ([Cost per trial](#cost-per-trial)).
- With Codex Luna (earlier cohort), Finitact succeeded in 82 of 90 short-task trials and windows-mcp in 63 of 90.
- In the earlier September 30 run, typed text from one windows-mcp trial reached the operator's terminal instead of the
  target window ([Safety observation](#safety-observation)).

## Setup

| Item | Value |
| --- | --- |
| Calling agent | Claude Code 2.1.288 (`claude-sonnet-5-5`). Same system prompt, task text, preparation, budgets and timeout for both systems. Codex cohort: `gpt-6-luna` (below). |
| Prompt | C1: the goal and the target window title. C2: the goal and one sentence on the open windows and pages. No ids, budgets, task-specific hints or tool instructions for either system. |
| windows-mcp | 0.8.5; its non-UI tools (PowerShell, FileSystem, Registry, Process, Clipboard, Scrape, Notification) disabled so the task has to be done through the UI |
| Finitact | commit `95d2eac` (E2E-02: `f743920`), its 7 MCP tools only; decision model TypeSafe `jev-latest` |
| Machine | Windows 11 Home (build 26200), Ryzen 7 7700, 24 GiB RAM, RTX 5070 12 GiB; the calling agent runs in WSL2 |
| Success | an oracle independent of both systems (UI Automation read, file on disk, browser state), not the agent's own report |
| Time | seconds from launching the calling agent to its exit |
| Tokens | the calling agent's input (including cache reads and cache writes) plus output |

The harnesses are `scripts/run_phase_i_comparison.py` (short tasks, cases in
`docs/evaluations/windows-mcp-replacement/case-manifest.json`) and `scripts/run_e2e_live.py` (multi-step tasks).
Each trial starts from a fresh target state. Trials run one after another on the same desktop, never in parallel.

Trial handling: a trial cut off by the calling agent's usage limit is invalid and was rerun (one windows-mcp Tk
scroll trial). A launcher fault that stopped before any trial was rerun as a whole set. The first Blender sets ran
beside a manually opened Blender with the same window title, and the first Finitact E2E-01 set started with other
windows covering the desktop, unlike its control; both were retaken on the same commit and only the retakes count.
Jev failures inside a trial count against Finitact.

## Short Windows tasks (C1, Claude)

N=10 per system and task. Each cell lists **Finitact / windows-mcp**; times and tokens are medians.

| Task | Success F / W | Time F / W | Tokens F / W | Token ratio |
| --- | ---: | ---: | ---: | ---: |
| Notepad: fill text | 10/10 / 10/10 | 7.0 / 31.4 s | 10.6 / 33.1k | 3.1× |
| Calculator: press 7 | 10/10 / 10/10 | 8.2 / 28.9 s | 10.4 / 29.4k | 2.8× |
| VS Code: replace text, save | 10/10 / 10/10 | 13.6 / 44.4 s | 11.3 / 88.6k | 7.8× |
| Tk: scroll list, select row | 10/10 / 10/10 | 14.2 / 38.0 s | 23.2 / 66.7k | 2.9× |
| Tk: drag card to zone | 10/10 / 10/10 | 8.2 / 18.1 s | 10.7 / 37.0k | 3.5× |
| Unity: open dropdown | 10/10 / 10/10 | 9.8 / 10.1 s | 10.6 / 35.0k | 3.3× |
| Unity: select resolution | 9/10 / 10/10 | 14.9 / 21.5 s | 10.9 / 52.7k | 4.8× |
| Blender: switch workspace | 10/10 / 10/10 | 17.0 / 14.6 s | 16.6 / 34.9k | 2.1× |
| Blender: select mode | 10/10 / 10/10 | 33.1 / 21.2 s | 11.5 / 42.0k | 3.6× |

The Finitact failure on Unity resolution was Jev's API returning a connection error. One Blender select-mode trial
also ended on an invalid Jev response, but the oracle confirmed the result.

![Median time per short task](figures/c1-time.svg)
![Median calling-agent tokens per short task](figures/c1-tokens.svg)

With Finitact the calling agent made one tool call on 8 tasks and two on the Tk scroll list (median), over 2 to 4
turns. With windows-mcp it made 1 to 10 UI tool calls over 4 to 15 turns, and each call returns a desktop snapshot that
stays in its context. That is where the token difference comes from.

Finitact was slower on both Blender tasks. Blender exposes almost no accessibility tree, so Finitact reads its
controls by OCR and asks Jev at each step (median 10 and 22.5 Jev calls,
[ADR-0044](../adr/0044-screen-path-uia.md)).

### Codex Luna cohort (2026-10-01, separate)

Same nine tasks, N=10, commit `83a5095` for all nine, calling agent `gpt-6-luna` through the ChatGPT login path
(without `OPENAI_API_KEY` or `CODEX_API_KEY`); subscription cost cannot be inferred from the token counts. Both
systems received the same goal and target-window-title prompt.

| Task | Success F / W | Time F / W | Tokens F / W |
| --- | ---: | ---: | ---: |
| Notepad: fill text | 10/10 / 3/10¹ | 17.0 / 44.3 s | 61.9 / 83.8k |
| Calculator: press 7 | 9/10 / 8/10 | 14.3 / 35.1 s | 54.0 / 61.9k |
| VS Code: replace text, save | 5/10 / 0/10 | 33.2 / 40.8 s | 125.2 / 123.3k |
| Tk: scroll list, select row | 8/10 / 6/10 | 32.8 / 78.8 s | 152.1 / 140.7k |
| Tk: drag card to zone | 10/10 / 10/10 | 20.5 / 41.9 s | 86.0 / 78.7k |
| Unity: open dropdown | 10/10 / 10/10 | 22.1 / 35.5 s | 103.7 / 81.2k |
| Unity: select resolution | 10/10 / 8/10 | 37.2 / 44.7 s | 161.5 / 123.3k |
| Blender: switch workspace | 10/10 / 9/10 | 32.7 / 41.2 s | 123.1 / 80.5k |
| Blender: select mode | 10/10 / 9/10 | 37.8 / 69.2 s | 163.9 / 99.6k |

¹Three windows-mcp Notepad trials were externally undetermined because the target window changed during the run;
the seven scored trials were 3 successes and 4 failures. Finitact was faster on all 9 median times, while the calling
agent's tokens were higher with it on seven tasks. The figures above use the Claude rows only.

## Multi-step tasks (C2, Claude)

N=5 per system and task. Each cell lists **Finitact / windows-mcp**; times and tokens are medians over all five trials.

| Task | Success F / W | Time F / W | Tokens F / W | Token ratio |
| --- | ---: | ---: | ---: | ---: |
| E2E-01 launch a chat app, post a message | 4/5 / 5/5 | 27.4 / 22.7 s | 38.5 / 50.7k | 1.3× |
| E2E-02 build an AWS Pricing Calculator estimate, save CSV | 5/5 / 5/5 | 75.4 / 142.0 s | 453 / 1127k | 2.5× |
| E2E-03 pick a saved email in the browser's autofill | 5/5 / 5/5 | 15.7 / 37.3 s | 32.9 / 77.3k | 2.4× |
| E2E-04 open Edge from the taskbar, search Wikipedia, scroll to the end | 5/5 / 5/5 | 60.4 / 43.5 s | 149.1 / 119.3k | 0.80× |
| E2E-05 create a folder in Explorer, drag two files into it from another window | 5/5 / 5/5 | 47.1 / 75.3 s | 104.5 / 334.4k | 3.2× |

![Median time per multi-step task](figures/c2-time.svg)
![Median calling-agent tokens per multi-step task](figures/c2-tokens.svg)

E2E-04 also checks the route, not just the final page: the taskbar was used, and the test-owned Edge profile's
history shows a search URL followed by the article URL. All ten trials passed that check.

The E2E-01 failure: Finitact refused to send input three times because the desktop had not been idle for one second
before each input ([ADR-0032](../adr/0032-screen-idle-1.md)). The harness does not synthesize input, so the source of
the activity is unknown; the stop is the designed behavior and is counted as a failure.

![Time: windows-mcp ÷ Finitact](figures/ratio-time.svg)
![Calling-agent tokens: windows-mcp ÷ Finitact](figures/ratio-tokens.svg)

### E2E-02 retake

At `95d2eac` Finitact succeeded 2/5 on E2E-02 (196.7 s, 1335k tokens). All three failures stopped at the same field
(SQS requests): the calling agent drove Chrome as a Windows window (`run_windows`), where that number input has no
accessibility name, and Finitact stopped below its confidence threshold instead of typing. Commit `f743920` routes a
`run_windows` call on a CDP-connected Chrome window to its tab ([ADR-0051](../adr/0051-run-windows-browser-tab-browser.md),
[ADR-0052](../adr/0052-window-browser-route-case.md)), and the harness turns that route on for every case. Only
E2E-02 Finitact was retaken, because no other case reaches a CDP browser; the table shows the retake. The other
rows stay at `95d2eac`. The route requires Chrome started with a remote-debugging port (README prerequisites).

## Jev usage inside Finitact

The calling-agent tokens above do not include Finitact's own calls to Jev. Medians per trial:

| Task | Jev calls | Jev tokens |
| --- | ---: | ---: |
| Notepad / Calculator | 2 / 2 | 10.2k / 32.4k |
| VS Code | 5.5 | 74.3k |
| Tk scroll / drag | 9 / 3 | 59.0k / 5.3k |
| Unity dropdown / resolution | 4 / 8 | 43.1k / 90.8k |
| Blender workspace / mode | 10 / 22.5 | 197.5k / 363.2k |
| E2E-01 / 02 / 03 / 04 / 05 | 15 / 83 / 5 / 18 / 42 | 197k / 228k / 52k / 316k / 549k |

Calls that Finitact resolves by rule or by a `ref` count as attempts but send no tokens. Their price is in
[Cost per trial](#cost-per-trial).

## Cost per trial

Median USD per trial. Finitact is the calling agent plus Jev plus the text model it uses for text-entry goals;
windows-mcp is the calling agent only. Per-trial values are `outer_cost_usd` and `provider_cost_usd` in
`data/c1-windows.jsonl` and `data/c2-e2e.jsonl`.

| Task | Finitact (agent + Jev/text) | windows-mcp | Ratio |
| --- | ---: | ---: | ---: |
| Notepad / Calculator | 0.006 (0.006 + 0.000) / 0.006 (0.005 + 0.001) | 0.035 / 0.021 | 5.7× / 3.3× |
| VS Code | 0.012 (0.010 + 0.003) | 0.078 | 6.4× |
| Tk scroll / drag | 0.017 (0.015 + 0.002) / 0.009 (0.008 + 0.000) | 0.061 / 0.041 | 3.6× / 4.9× |
| Unity dropdown / resolution | 0.008 (0.006 + 0.002) / 0.011 (0.007 + 0.003) | 0.031 / 0.051 | 4.0× / 4.8× |
| Blender workspace / mode | 0.017 (0.010 + 0.007) / 0.028 (0.010 + 0.013) | 0.026 / 0.049 | 1.5× / 1.8× |
| E2E-01 / 02 / 03 | 0.035 / 0.266 / 0.032 | 0.054 / 0.555 / 0.067 | 1.5× / 2.1× / 2.1× |
| E2E-04 / 05 | 0.096 / 0.096 | 0.113 / 0.206 | 1.2× / 2.1× |

Finitact cost less on all 14 tasks: 1.5× to 6.4× on the short tasks and 1.2× to 2.1× on the multi-step ones. Jev and
the text model are 2% to 46% of Finitact's median cost (Blender select mode is the largest). On E2E-04 the calling agent used
more tokens with Finitact but cost less, because windows-mcp wrote about twice as much new context to the prompt cache
(median 20.0k versus 10.6k tokens), and a cache write costs 20 times a cache read.

Prices (per million tokens, 2026-10-04): `claude-sonnet-5-5` input $2, cache write $4, cache read $0.20, output $10.
Claude Code writes the cache with the 1-hour lifetime; at these prices the totals match the cost Claude Code itself
reported for every C2 trial. Jev `jev-1.13.0` bills input only, $0.042; output is free
([TypeSafe models](https://docs.typesafe.ai/models)). The text model `deepseek-chat` is priced at its peak rate
(input $0.30 on a cache miss, $0.006 on a hit, output $1.20), an upper bound since the off-peak rate is half
([DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing)). Subscription plans are not modeled.

## Safety observation

In the September 30 run, during E2E-05 with windows-mcp, the folder name the calling agent typed (`batch-0928`)
appeared in the input line of the operator's own Claude Code terminal, a window outside the task. In 4 of 5 trials
the agent issued a `Type` in parallel with clicks and a drag on another window in the same turn; windows-mcp runs such
calls concurrently and `Type` does not check which window has focus when it types. Which call leaked could not be
identified from the logs.

Finitact did not type anything outside the target in any trial. Before each input it checks that the target window is
in front and that the user has not used the keyboard or mouse for a set time, and it stops if the screen changed
since the observation ([ADR-0032](../adr/0032-screen-idle-1.md)). This is one incident, not a measured rate, but it is
the kind of error that a single-shot score does not show.

## Limits

- One machine and one calling-agent model per cohort. The numbers describe these tasks here, not a general
  performance difference.
- N=10 and N=5 are small: 4/5 versus 5/5 is not distinguishable, 2/5 versus 5/5 is a signal but not a rate.
- The tasks were written by the Finitact author, and Finitact was changed on some of them before this run.
  windows-mcp was not tuned.
- Tk and the E2E fixtures are the author's own test targets. Unity opens a fixed project; Blender starts with factory
  settings.
- Jev tokens are excluded from the token comparison but included in the cost comparison. Costs use list prices on one
  date; a price change on either side changes the ratio.
