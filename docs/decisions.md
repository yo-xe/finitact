# Design decisions

> Contents as of 2026-10-04. 日本語: [decisions.ja.md](decisions.ja.md)

The decision records in `docs/adr/` are written in Japanese and are append-only, so they include how each decision
was reached, in date order. This page groups the decisions that are in force today by topic and links each one to its record. If this
page disagrees with a record or with the code, the record's latest addendum and the code win.

## Records no longer in force

| ADR | Status | Reason |
|---|---|---|
| [ADR-0011](adr/0011-screen-two-action-lease-scope.md) | Superseded | Replaced by [ADR-0012](adr/0012-screen-2-owned-popup-lease-run.md) |
| [ADR-0028](adr/0028-screen-fill-observe-caret-frame.md) | Withdrawn | Re-capturing removed the misreads but raised the median time from 20.0 s to 27.6 s, so it was reverted |

Decision 2 of [ADR-0048](adr/0048-browser-boundary-screen-handoff.md) (same-origin iframes as CDP candidates) was rejected.

## 1. Direction and scope

- The basic structure repeats three steps: pick one action from a finite set of observed candidates, perform it, observe again. The decision provider is not limited to Jev ([ADR-0001](adr/0001-project-inception.md), [ADR-0002](adr/0002-mvp.md), [ADR-0003](adr/0003-finitact-name-and-direction.md)).
- First make the whole path from the MCP call to sending input work with TypeSafe, then compare providers ([ADR-0004](adr/0004-mcp-before-provider-comparison.md)).
- The common provider contract returns only a candidate ID or a terminal reason. Probabilities, multiple heads and sending input are outside the contract ([ADR-0005](adr/0005-provider-neutral-decision-contract.md)).
- Finitact does not use app-specific means (`bpy`, the Unity Editor API, …) itself; the caller supplies them. Finitact implements only app-independent screen analysis (UIA, OCR, …). The caller supplies them in two ways: annotations on candidates (`app_annotations`) and state written by the app, used to verify the outcome (`app_expect`) ([ADR-0053](adr/0053-adr-0017-feed.md)).

## 2. Safe synthetic input

- Synthetic input is protected by three mechanisms: a cross-process named mutex, a check before starting that the user is not using the mouse or keyboard (idle check), and a visible indicator while running. No mechanism is claimed to guarantee more than it does ([ADR-0008](adr/0008-named-mutex-live.md), [ADR-0009](adr/0009-synthetic-input.md), [ADR-0010](adr/0010-indicator-win32-layered-overlay-theme.md)).
- An abandoned mutex is remembered per desktop inside the process, and further input is refused until the server restarts ([ADR-0013](adr/0013-interaction-mutex-desktop-process-sticky.md)).
- The idle threshold is configurable, 1 second by default. When the latest input came only from the run itself, the next run keeps the same idle start time ([ADR-0021](adr/0021-run-idle-run.md), [ADR-0032](adr/0032-screen-idle-1.md)).
- The target window is forced to the foreground right before input is sent, and it is sent only after the foreground and hit-test checks pass. A refusal decided before any input does not mark the environment blocked ([ADR-0033](adr/0033-screen.md)).
- For two-step actions (open a popup, then choose), observation extends to owned popups of the same PID and owner chain, and the input lock is held for the whole run ([ADR-0012](adr/0012-screen-2-owned-popup-lease-run.md), replaces [ADR-0011](adr/0011-screen-two-action-lease-scope.md)).
- Dragging between windows is limited to a two-window contract (start window and drop window). The protected-window rules apply to the drop window too ([ADR-0045](adr/0045-cross-window-drag.md)).
- When it is unclear whether input arrived, it is never resent automatically and the environment is blocked ([ADR-0006](adr/0006-windows-adapter.md) operating decision, [ADR-0013](adr/0013-interaction-mutex-desktop-process-sticky.md)).

## 3. Observation and candidate sources

- On the Windows screen path, named UIA elements are the primary candidate source, and OCR produces candidates only for regions where UIA returns no elements. Windows where UIA returns no elements are read with OCR over the whole window ([ADR-0036](adr/0036-screen-path-com-uia-ocr.md), [ADR-0044](adr/0044-screen-path-uia.md)).
- The default OCR is PP-OCRv6 medium with OpenVINO. Edge candidates whose centre falls inside an OCR box are dropped ([ADR-0007](adr/0007-stage1-extractor.md), [ADR-0016](adr/0016-stage1-ocr-pp-ocrv6-medium-openvino.md), [ADR-0018](adr/0018-stage1-ocr-edge.md)).
- Scroll candidates are up/down per list block; fill candidates are one per uniform-colour panel ([ADR-0023](adr/0023-screen-path-scroll-list-up-down.md), [ADR-0029](adr/0029-screen-fill-panel-block.md)).
- Elements that are only hidden by a scroll area are offered as "scroll first" candidates, at most 40, nearest first; before pressing, Finitact checks they are visible and on top ([ADR-0038](adr/0038-decision.md)).
- The calling agent specifies one kind of target, `window:<HWND>:<PID>`. `synthetic_input_allowed` selects the path: true is the screen path, false is pattern-only UIA ([ADR-0040](adr/0040-windows-target-1.md)).

## 4. Division of work with the decision provider

- Finitact guarantees, with evidence, that input can be sent to a candidate and that doing so is safe. Jev picks, among those candidates, the one that fits the goal; its judgement is used only to reorder candidates or to stop ([ADR-0030](adr/0030-finitact-jev.md)).
- Low-confidence choices and BLOCKED below confidence 0.6 are returned to the calling agent as `provider_uncertain`. The calling agent then names a candidate directly; it rephrases the goal only when it cannot name one ([ADR-0019](adr/0019-bug-0023-label-agent.md), [ADR-0020](adr/0020-blocked-0-6-provider-uncertain.md), [ADR-0022](adr/0022-provider-uncertain-agent.md)).
- Exact per-goal labels (`click_label_constraints`), `fill_values` and `find_and_click`, which uses no provider, let a goal run without Jev's judgement ([ADR-0024](adr/0024-screen-click-goal-label.md), [ADR-0026](adr/0026-screen-fill-goal-fill-values.md), [ADR-0027](adr/0027-label-click-find-and-click-provider.md)).
- Running without Jev's judgement is reduced to one argument: a goal `ref` taken from an observation ([ADR-0041](adr/0041-observe-window-ref-pick.md), [ADR-0042](adr/0042-run-windows-goal-observe-ref.md), [ADR-0043](adr/0043-jev-goal-ref.md)).

## 5. Deciding success

- A provider's DONE is not proof of success. The success check combines facts Finitact computes (send result, label differences, changed pixel areas) with Jev's judgement of fit ([ADR-0030](adr/0030-finitact-jev.md) addenda).
- The success check also covers scroll. A screen click does not count as success from a pixel change alone; it needs evidence of a concrete end state ([ADR-0031](adr/0031-e-scroll.md), [ADR-0030](adr/0030-finitact-jev.md) addendum 4).
- Committing actions such as launching or sending succeed only with end-state evidence tied to the target AND Jev's completion judgement ([ADR-0035](adr/0035-decision.md)).
- Postconditions are checked against the control's expected state (checkbox toggled, select value, fill value; on Windows, UIA toggle and selection-item states) and returned as facts: `met / not_met / unknown` ([ADR-0039](adr/0039-decision.md)).
- Without evidence the result stays `unverified`, never a false success.

## 6. Tool arguments and results for the calling agent

- Arguments of `run_windows` that have defaults can be omitted, and the target can be given as a window title. Evaluation-only arguments are left out of the tool definition ([ADR-0034](adr/0034-run-windows-screen.md), [ADR-0050](adr/0050-windows-target-tool.md)).
- Item refs from `observe_window` can be run directly through a goal `ref` ([ADR-0041](adr/0041-observe-window-ref-pick.md), [ADR-0042](adr/0042-run-windows-goal-observe-ref.md)).
- Results report the termination reason, mutation state and outcome verification separately ([ADR-0004](adr/0004-mcp-before-provider-comparison.md)). Calling again with the same `run_id` returns the recorded result without repeating input ([ADR-0006](adr/0006-windows-adapter.md), [ADR-0051](adr/0051-run-windows-browser-tab-browser.md)).

## 7. Browser path

- Browser and Windows use the same implementation for control (budgets, using each decision once, re-observation, determining whether input arrived, records); candidate sources, checking that the observation is current, sending input and success evidence are implemented per path ([ADR-0037](adr/0037-browser-windows.md)).
- Drag is opt-in with `extra_operations=["drag"]` and is sent in two steps ([ADR-0046](adr/0046-browser-drag-opt-in-2-drag.md)). JS dialogs are handled as finite candidates ([ADR-0047](adr/0047-browser-js-dialog.md)).
- Results report how many elements the browser path cannot operate (iframes, open shadow roots, popup links) and the `screen_target` of the window showing the tab, so the calling agent can switch to the screen path ([ADR-0048](adr/0048-browser-boundary-screen-handoff.md)).
- `run_windows` sends a page goal in a browser window to the browser path only when the conditions hold and it is explicitly opted in (`routing=browser_if_singleton`, `FINITACT_WINDOW_BROWSER_ROUTE=1`) ([ADR-0051](adr/0051-run-windows-browser-tab-browser.md)).

## 8. Evaluation and publication

- Comparisons use the public configuration, and both systems are scored by the same check, which reads the target app's end state outside Finitact. Public MCP results are not rewritten ([ADR-0014](adr/0014-phase-i-outcome-mcp-oracle.md), [ADR-0015](adr/0015-phase-i.md)).
- The public comparison runs every case with the window→browser route on, and the README states this ([ADR-0052](adr/0052-window-browser-route-case.md)).
- The public repo receives only allowlisted files, copied one way from the development repo onto a separate, new git history; the evaluation is published as a report. Outside PRs are taken into the development repo as patches ([ADR-0049](adr/0049-repo-export.md)).
