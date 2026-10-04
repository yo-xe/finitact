# Known issues

> Contents as of 2026-10-04. 日本語: [known-issues.ja.md](known-issues.ja.md)

The bug records in `docs/bugs/` are written in Japanese and are append-only; they keep the reproduction notes from when
each bug was found. This page explains the open issues by their effect on users and indexes the fixed ones by area.
Please report new problems as GitHub issues.

## Open (7)

### Issues that affect using Finitact

- **[BUG-0023](bugs/0023-screen-210-blocked-0-2.md) On screens with many candidates, Jev picks BLOCKED instead of the right one (medium)**
  On the Windows screen path with about 210 candidates, BLOCKED can win as the relative maximum at a low probability
  (around 0.2) and the run stops without acting. BLOCKED below confidence 0.6 is returned to the calling agent as
  `provider_uncertain` with the candidate list, so the calling agent can continue by naming a candidate with a goal `ref`
  ([ADR-0020](adr/0020-blocked-0-6-provider-uncertain.md), [ADR-0022](adr/0022-provider-uncertain-agent.md)). Unity's "16:9 Aspect" resolution item still fails this way because Jev lacks the app knowledge.
- **[BUG-0073](bugs/0073-run-windows-chrome-aws-sqs-uia.md) The screen path cannot fill an unlabelled number field in a browser window (medium)**
  A number field with no UIA name and only a placeholder, such as those in the AWS Pricing Calculator in Chrome, stops at
  fill confidence 0.14–0.39 with the default `run_windows` screen path. When the browser window meets the conditions,
  `FINITACT_WINDOW_BROWSER_ROUTE=1` (or `routing=browser_if_singleton`) sends the goal to the browser path, which fills it ([ADR-0051](adr/0051-run-windows-browser-tab-browser.md)).
- **[BUG-0068](bugs/0068-browser-wheel-2.md) In a background Chrome tab, the second of consecutive wheel scrolls does not move (medium)**
  When wheel input is sent to the same background tab through CDP, the second wheel event never reaches the page, though
  CDP reports success. The third one moves. Bringing the tab to the front avoids it but hides other tabs in the same
  Chrome, so it is not used as the product fix. Check the position in the next observation after scrolling.
- **[BUG-0074](bugs/0074-bug-0043-run-browser-run-runti.md) The first `run_browser` call can fail while its helper daemon starts (low)**
  The first run fails with `RuntimeError('listening on 127.0.0.1:…')`. Calling again with the same request succeeds.
  A recurrence of [BUG-0043](bugs/0043-run-browser-run-daemon-64-runt.md).

### Issues in the evaluation and the compared tool (Finitact's behaviour is not affected)

- **[BUG-0071](bugs/0071-e2e-04-oracle.md) The E2E-04 (taskbar → search → article) judge passed a trial that skipped the search (high)**
  Route evidence is now `pass/fail/unknown`, and old results without an independent `pass` were withdrawn. E2E-04
  numbers are not published until they are retaken.
- **[BUG-0062](bugs/0062-e2e-04-edge-uia-found-false-wi.md) The E2E-04 judge misses the end of an Edge article through UIA and fails a successful trial (medium)**
  Re-scanning was added, but no trial has needed it yet, so the fix is unconfirmed.
- **[BUG-0063](bugs/0063-c2-e2e-05-windows-mcp-batch-09.md) The compared tool's (windows-mcp) `Type` typed into the operator's terminal (medium)**
  windows-mcp does not check the foreground window while typing; when another action running in parallel moves the
  foreground, the text goes to another window. Finitact checks foreground, hit-test and idle on the target window right
  before sending input, so it has no such path ([ADR-0032](adr/0032-screen-idle-1.md), [ADR-0033](adr/0033-screen.md)). The report lists this under safety observations.

## Accepted limitations

- **OCR fill without a readable caret is not confirmed before typing.** In apps that draw their own text fields and expose
  no caret (Blender, Unity), an OCR fill candidate is clicked, then Ctrl+A and the text are sent without first confirming
  that the field accepted focus ([ADR-0030](adr/0030-finitact-jev.md) addendum 5). Fields that UIA or a caret can confirm
  (Notepad, VS Code, Discord, Chrome) are not affected. Check the field value in the next observation.
- **A correct row selection in a Tk Listbox is reported as `unverified`**, because the selection state cannot be read back.
- **Finitact does not check that an `app_expect` feed is truthful.** The feed is the responsibility of the caller and the
  app-side plugin; only a matching value written after the action counts as `outcome_verified`, so a wrong feed can produce
  a wrong verdict ([ADR-0053](adr/0053-adr-0017-feed.md)).

## Index of fixed and won't-fix records (67)

### Browser path

- [BUG-0001](bugs/0001-browser-mutation-history.md) A mutation that failed midway was missing from the history
- [BUG-0002](bugs/0002-bug.md) An expected MutationUncertain message narrowed a test's sub-cases
- [BUG-0004](bugs/0004-nested-scroll-control-popup-li.md) Controls outside a nested scroll and popup links were offered as plain click candidates
- [BUG-0005](bugs/0005-wikipedia-action-table.md) A transparent language selector was missing from the action table
- [BUG-0042](bugs/0042-run-browser-cdp-browser-chrome.md) Without a CDP browser, failing took about 120 seconds
- [BUG-0043](bugs/0043-run-browser-run-daemon-64-runt.md) The first run failed while waiting for the daemon (recurred as [BUG-0074](bugs/0074-bug-0043-run-browser-run-runti.md))
- [BUG-0044](bugs/0044-run-browser-goal-search-all-se.md) A report-only goal led to unrequested clicks
- [BUG-0045](bugs/0045-run-browser-js-dialog-confirm-.md) An open JS dialog made later operations time out
- [BUG-0064](bugs/0064-c2-e2e-02-finitact-agent-claud.md) Standard input constraints of a field were reported as a refusal

### Windows synthetic input and safety

- [BUG-0012](bugs/0012-automationindicator-show-ws-ex.md) The run indicator stole the foreground
- [BUG-0014](bugs/0014-windows-mutex-owner-crash-is-i.md) A crash of the mutex owner went unnoticed when no other handle existed
- [BUG-0015](bugs/0015-after-wait-abandoned-the-serve.md) Ownership of an abandoned mutex stayed on a pool thread
- [BUG-0017](bugs/0017-125-screen-grounded-pointer-ca.md) At 125% display scaling the pointer clicked elsewhere
- [BUG-0025](bugs/0025-screen-run-finitact-synthetic-.md) The run's own synthetic click reset the idle time and stopped the next run
- [BUG-0033](bugs/0033-screen-fill-pointer-hover-tool.md) Fill chose a hover tooltip and replaced the document text
- [BUG-0034](bugs/0034-tk-entry-name-oldvalue-88x21-f.md) Fill into a small Entry was sent to its label
- [BUG-0036](bugs/0036-screen-fill-caret-bug-0033-dis.md) The caret check refused an empty field that already had focus
- [BUG-0041](bugs/0041-agent-run-windows-windows-term.md) The calling agent could operate the harness's own terminal (target restrictions added)
- [BUG-0055](bugs/0055-fill-set-clipboard-null-block.md) An empty fill failed to set the clipboard and blocked the environment

### Windows taskbar, Start and search

- [BUG-0040](bugs/0040-taskbar-shell-traywnd-uia-ocr-.md) Taskbar pinned icons were not candidates and clicks could not bring the taskbar forward
- [BUG-0047](bugs/0047-start-list-windows-taskbar-she.md) Opening Start/search removed the taskbar from the window list
- [BUG-0048](bugs/0048-start-click-searchhost-foregro.md) After a Start click, both the search and Start windows were refused as targets
- [BUG-0051](bugs/0051-taskbar-click-post-mutation-ob.md) Re-observation after a Start click failed
- [BUG-0052](bugs/0052-settings-applicationframewindo.md) With Settings in front, the taskbar Start click did not arrive
- [BUG-0053](bugs/0053-taskbar-fill-searchhost-key-bl.md) Filling the taskbar search box moved the foreground and lost the keys
- [BUG-0054](bugs/0054-searchhost-taskbar-click-shell.md) With the search window in front, taskbar clicks were refused
- [BUG-0057](bugs/0057-dwm-cloaked-2-searchhost-corew.md) A hidden search window holding the foreground blocked taskbar operations
- [BUG-0058](bugs/0058-taskbar-key-goal-press-enter-e.md) Enter on the taskbar did not recognise success and repeated

### Observation and candidates

- [BUG-0013](bugs/0013-warm-up-native-extractor-runti.md) The extractor hung on its first native load
- [BUG-0018](bugs/0018-stage1-tesseract-ocr-popup-1-t.md) Tesseract read no text in small popups
- [BUG-0020](bugs/0020-pp-ocr-extractor-1-valueerror.md) The OCR extractor raised on images without text
- [BUG-0026](bugs/0026-provider-uncertain-screen-labe.md) Labels returned to the calling agent contained mechanical region names
- [BUG-0031](bugs/0031-screen-fill-ocr-dldvalue-jev-3.md) A fill candidate scored low because of an OCR misread, and fill went elsewhere
- [BUG-0032](bugs/0032-text-caret-ocr-oldvalue-dldval.md) A lit text caret garbles OCR on its line (won't fix: re-capturing cost speed and [ADR-0028](adr/0028-screen-fill-observe-caret-frame.md) was withdrawn)
- [BUG-0037](bugs/0037-uia-provider-uncertain-screen-.md) Refs returned on the UIA path could not be used
- [BUG-0046](bugs/0046-run-windows-win11-uia-document.md) The Windows 11 Notepad document was not a fill candidate
- [BUG-0049](bugs/0049-observe-window-screen-ref-synt.md) Refs from observe_window failed in runs with `synthetic_input_allowed=false`
- [BUG-0050](bugs/0050-uia-set-range-tool-runs-py-fil.md) UIA range setting (sliders) was unreachable
- [BUG-0056](bugs/0056-screen-windows-uia-slider-obse.md) UIA sliders were not candidates on the screen path
- [BUG-0061](bugs/0061-c1-vs-code-fill-save-finitact-.md) Refilling a field with the value it already showed used up the budget
- [BUG-0065](bugs/0065-run-windows-list-windows.md) A minimised window with the same title made title targets ambiguous
- [BUG-0066](bugs/0066-unity-dropdown-goal-target-sta.md) After a dropdown opened, sending repeated

### Decision provider and model calls

- [BUG-0003](bugs/0003-model-call-budget-decision-pro.md) The model-call budget counted only successful decisions
- [BUG-0019](bugs/0019-ollamadecisionprovider-prompt-.md) A local provider silently truncated prompts longer than its context
- [BUG-0021](bugs/0021-screen-typesafe-255-http-400-m.md) More than 255 candidates stopped with HTTP 400
- [BUG-0022](bugs/0022-screen-210-typesafe-http-400-m.md) 210 or more candidates exceeded the token limit
- [BUG-0029](bugs/0029-windows-mcp-server-fill-text-h.md) The text helper sometimes returned no value at default temperature

### Deciding success

- [BUG-0039](bugs/0039-popup-role-option-e.md) Choices inside a vanishing popup were outside the achievement check
- [BUG-0069](bugs/0069-scroll-item-15-outcome-verifie.md) Selecting a different row during scroll search was reported as verified

### Runtime and MCP transport

- [BUG-0006](bugs/0006-powershellbridge-cp932-stderr-.md) The PowerShell bridge garbled CP932 error output
- [BUG-0007](bugs/0007-windows-native-python-cp932-fi.md) Import failed on Windows Python with the cp932 locale
- [BUG-0008](bugs/0008-test-stage1-extractors-py-linu.md) A test used a Linux-only font path
- [BUG-0009](bugs/0009-tkinter-2-tk-thread-windows-fa.md) Re-creating Tk on another thread crashed on Windows
- [BUG-0010](bugs/0010-run-windows-screen-mcp-stdio-t.md) Candidate extraction hung under stdio MCP
- [BUG-0011](bugs/0011-run-windows-screen-mcp-stdio-t.md) Waiting for the indicator hung under stdio MCP
- [BUG-0028](bugs/0028-run-windows-stdio-mcp-ledger-r.md) A run stayed "running" forever after the MCP connection dropped
- [BUG-0035](bugs/0035-e-live-tk-second-second-entry-.md) Python crashed natively during a live achievement check
- [BUG-0067](bugs/0067-codex-run-windows-mcp-transpor.md) With Codex as the calling agent, the transport closed and the server was left running

### Evaluation harness

- [BUG-0016](bugs/0016-unity-dropdown-probe-escape-un.md) Cleanup after the Unity dropdown probe left the popup open
- [BUG-0024](bugs/0024-blender-oracle-state-writer-os.md) Blender's state writer stopped on a file-replace permission error
- [BUG-0027](bugs/0027-phase-i-unity-open-case-resolu.md) The Unity case did not fix the starting resolution
- [BUG-0030](bugs/0030-phase-i-tk-scroll-oracle-state.md) The Tk scroll judge read a half-written empty file
- [BUG-0038](bugs/0038-e2e-03-prepare-brave-30-timeou.md) Preparation timed out after 30 seconds when Brave was not running
- [BUG-0059](bugs/0059-phase-i-runner-screen-case-cou.md) Unattended, screen-case preparation could not bring windows forward and failed every case
- [BUG-0060](bugs/0060-case-oracle-ocr-windows-mcp-ru.md) The Calculator judge could not read the display after key input
- [BUG-0070](bugs/0070-claude-is-error-true-subtype-s.md) Trials ended by the Claude usage limit counted as valid
- [BUG-0072](bugs/0072-e2e-04-edge-profile-windows-si.md) Synced history in the test Edge profile could make the route check pass falsely
