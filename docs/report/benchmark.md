# Finitact versus windows-mcp

The Claude rows were measured on 2026-10-04 with one Finitact code snapshot (`e7b9cf0`, the last change to `finitact/`
before the run) and the same prompt, preparation, calling-agent model and budgets for both systems. The multi-step runs
followed `79d1b0f`, which changed only how the evaluation's calling-agent wrapper marks a Jev connection failure. The
Codex Luna rows are an earlier, separate cohort (2026-10-01, commit `83a5095`) with a different calling-agent model;
keep the two apart when interpreting differences.

## Summary

- Short Windows tasks (C1, Claude): windows-mcp succeeded 10/10 on all 9 tasks; Finitact 10/10 on 8 and 9/10 on
  Blender select mode. Finitact was faster on 7 of 9, and the calling agent used 1.4× to 6.5× fewer tokens with it on
  all 9.
- Multi-step tasks (C2, Claude): Finitact succeeded 5/5 on all 5. windows-mcp succeeded 5/5 on four and 4/5 on the
  Wikipedia task, where one trial reached the article without a recorded search. With Finitact the calling agent used
  fewer tokens on four (1.4× to 2.6×) and more on one (Wikipedia 1.4×); Finitact was faster on two.
- The token gap is widest where windows-mcp needs many observe-act turns (VS Code, Explorer drag) and reverses on the
  long Wikipedia task, where the calling agent drives Finitact goal by goal. Finitact spends extra tokens on Jev that
  the calling agent does not pay for; they are listed separately below.
- Counting Jev and the calling agent at list prices, a trial cost less with Finitact on 13 of 14 tasks: 1.3× to 6.3×
  on the short tasks and 1.4× to 1.9× on four multi-step ones; on Wikipedia it cost 1.1× more
  ([Cost per trial](#cost-per-trial)).
- With Codex Luna (earlier cohort), Finitact succeeded in 82 of 90 short-task trials and windows-mcp in 63 of 90.
- In the earlier September 30 run, typed text from one windows-mcp trial reached the operator's terminal instead of the
  target window ([Safety observation](#safety-observation)).

## Setup

| Item | Value |
| --- | --- |
| Calling agent | Claude Code 2.1.289 (`claude-sonnet-5-5`). Same system prompt, task text, preparation, budgets and timeout for both systems. Codex cohort: `gpt-6-luna` (below). |
| Prompt | C1: the goal and the target window title. C2: the goal and one sentence on the open windows and pages. No ids, budgets, task-specific hints or tool instructions for either system. |
| windows-mcp | 0.8.5; its non-UI tools (PowerShell, FileSystem, Registry, Process, Clipboard, Scrape, Notification) disabled so the task has to be done through the UI |
| Finitact | commit `e7b9cf0`, its 7 MCP tools only, window-to-browser route on for every case ([ADR-0052](../adr/0052-window-browser-route-case.md)); decision model TypeSafe `jev-latest` |
| Machine | Windows 11 Home (build 26200), Ryzen 7 7700, 24 GiB RAM, RTX 5070 12 GiB; the calling agent runs in WSL2 |
| Success | an oracle independent of both systems (UI Automation read, file on disk, browser state), not the agent's own report |
| Time | seconds from launching the calling agent to its exit |
| Tokens | the calling agent's input (including cache reads and cache writes) plus output |

The harnesses are `scripts/run_phase_i_comparison.py` (short tasks, cases in
`docs/evaluations/windows-mcp-replacement/case-manifest.json`) and `scripts/run_e2e_live.py` (multi-step tasks).
Each trial starts from a fresh target state. Trials run one after another on the same desktop, never in parallel.

Trial handling: a trial cut off by the calling agent's usage limit is invalid and is rerun. So is a Finitact trial
in which Jev's API returned a connection error before any UI action (three Unity dropdown trials, BUG-0076); a Jev
failure after an action, or any other Jev error, counts against Finitact. A set stopped by a fault outside both
systems was voided and rerun as a whole: in E2E-01, another window covering the desktop, the chat app's window never
appearing (once per system) and the calling agent's MCP start exceeding its 30-second default; in E2E-02 Finitact,
the harness not passing the browser's debugging endpoint, so the route was off; in E2E-04 Finitact, a stale input
lock left by a stopped run. Only the reruns count.

## Short Windows tasks (C1, Claude)

N=10 per system and task. Each cell lists **Finitact / windows-mcp**; times and tokens are medians.

| Task | Success F / W | Time F / W | Tokens F / W | Token ratio |
| --- | ---: | ---: | ---: | ---: |
| Notepad: fill text | 10/10 / 10/10 | 7.6 / 36.5 s | 12.2 / 41.4k | 3.4× |
| Calculator: press 7 | 10/10 / 10/10 | 8.6 / 30.1 s | 12.0 / 29.4k | 2.4× |
| VS Code: replace text, save | 10/10 / 10/10 | 13.4 / 47.2 s | 12.9 / 84.3k | 6.5× |
| Tk: scroll list, select row | 10/10 / 10/10 | 12.6 / 40.0 s | 26.3 / 66.5k | 2.5× |
| Tk: drag card to zone | 10/10 / 10/10 | 8.2 / 32.8 s | 12.3 / 43.9k | 3.6× |
| Unity: open dropdown | 10/10 / 10/10 | 10.7 / 10.9 s | 12.2 / 26.6k | 2.2× |
| Unity: select resolution | 10/10 / 10/10 | 21.1 / 23.0 s | 18.7 / 51.4k | 2.8× |
| Blender: switch workspace | 10/10 / 10/10 | 16.8 / 11.4 s | 19.0 / 26.6k | 1.4× |
| Blender: select mode | 9/10 / 10/10 | 43.3 / 32.5 s | 20.8 / 42.0k | 2.0× |

The Finitact failure on Blender select mode switched to Edit Mode, then reopened the dropdown and stopped below its
confidence threshold; the oracle failed it because the menu was still open at the end.

![Median time per short task](figures/c1-time.svg)
![Median calling-agent tokens per short task](figures/c1-tokens.svg)

With Finitact the calling agent made one tool call on 7 tasks and two on the Tk scroll list and Blender select mode
(median), over 2 to 4 turns. With windows-mcp it made 1 to 10 UI tool calls over 4 to 15 turns, and each call returns a desktop snapshot that
stays in its context. That is where the token difference comes from.

Finitact was slower on both Blender tasks. Blender exposes almost no accessibility tree, so Finitact reads its
controls by OCR and asks Jev at each step (median 10 and 33 Jev calls,
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
| E2E-01 launch a chat app, post a message | 5/5 / 5/5 | 26.7 / 21.6 s | 35.2 / 50.7k | 1.4× |
| E2E-02 build an AWS Pricing Calculator estimate, save CSV | 5/5 / 5/5 | 102.9 / 110.5 s | 551 / 1075k | 2.0× |
| E2E-03 pick a saved email in the browser's autofill | 5/5 / 5/5 | 12.6 / 32.1 s | 36.9 / 57.9k | 1.6× |
| E2E-04 open Edge from the taskbar, search Wikipedia, scroll to the end | 5/5 / 4/5 | 73.4 / 48.9 s | 169.2 / 119.0k | 0.70× |
| E2E-05 create a folder in Explorer, drag two files into it from another window | 5/5 / 5/5 | 41.6 / 34.8 s | 79.8 / 208.5k | 2.6× |

![Median time per multi-step task](figures/c2-time.svg)
![Median calling-agent tokens per multi-step task](figures/c2-tokens.svg)

E2E-04 also checks the route, not just the final page: the taskbar was used, and the test-owned Edge profile's
history shows a search URL followed by the article URL. Nine of ten trials passed that check. The windows-mcp failure
reached the article, but the history held no search visit, so the route could not be shown and the trial counts as
a failure under the rule fixed before the run (BUG-0071).

![Time: windows-mcp ÷ Finitact](figures/ratio-time.svg)
![Calling-agent tokens: windows-mcp ÷ Finitact](figures/ratio-tokens.svg)

E2E-02 runs in a Chrome started with a remote-debugging port. When the calling agent drives that Chrome as a
Windows window (`run_windows`), Finitact routes the call to the tab ([ADR-0051](../adr/0051-run-windows-browser-tab-browser.md));
without the route, the SQS request field has no accessibility name and Finitact stops below its confidence threshold.

## Jev usage inside Finitact

The calling-agent tokens above do not include Finitact's own calls to Jev. Medians per trial:

| Task | Jev calls | Jev tokens |
| --- | ---: | ---: |
| Notepad / Calculator | 2 / 2 | 10.2k / 32.4k |
| VS Code | 5 | 75.1k |
| Tk scroll / drag | 6 / 3 | 58.5k / 5.2k |
| Unity dropdown / resolution | 8 / 10.5 | 86.6k / 112.8k |
| Blender workspace / mode | 10 / 33 | 197.6k / 523.9k |
| E2E-01 / 02 / 03 / 04 / 05 | 9 / 91 / 5 / 21 / 40 | 181k / 308k / 51k / 288k / 577k |

Calls that Finitact resolves by rule or by a `ref` count as attempts but send no tokens. Their price is in
[Cost per trial](#cost-per-trial).

## Cost per trial

Median USD per trial. Finitact is the calling agent plus Jev plus the text model it uses for text-entry goals;
windows-mcp is the calling agent only. Per-trial values are `outer_cost_usd` and `provider_cost_usd` in
`data/c1-windows.jsonl` and `data/c2-e2e.jsonl`.

| Task | Finitact (agent + Jev/text) | windows-mcp | Ratio |
| --- | ---: | ---: | ---: |
| Notepad / Calculator | 0.007 (0.006 + 0.000) / 0.007 (0.005 + 0.001) | 0.035 / 0.021 | 5.2× / 3.1× |
| VS Code | 0.012 (0.010 + 0.003) | 0.078 | 6.3× |
| Tk scroll / drag | 0.017 (0.015 + 0.002) / 0.009 (0.009 + 0.000) | 0.061 / 0.045 | 3.6× / 5.1× |
| Unity dropdown / resolution | 0.009 (0.006 + 0.003) / 0.017 (0.010 + 0.004) | 0.032 / 0.051 | 3.4× / 3.0× |
| Blender workspace / mode | 0.018 (0.011 + 0.007) / 0.036 (0.018 + 0.018) | 0.030 / 0.049 | 1.7× / 1.3× |
| E2E-01 / 02 / 03 | 0.032 / 0.306 / 0.032 | 0.054 / 0.544 / 0.062 | 1.7× / 1.8× / 1.9× |
| E2E-04 / 05 | 0.128 / 0.101 | 0.112 / 0.142 | 0.88× / 1.4× |

Finitact cost less on 13 of 14 tasks: 1.3× to 6.3× on the short tasks and 1.4× to 1.9× on four multi-step ones. On
E2E-04 the calling agent used 1.4× more tokens with Finitact, and the trial cost 1.1× more. Jev and the text model are
2% to 44% of Finitact's median cost (Blender select mode is the largest).

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
