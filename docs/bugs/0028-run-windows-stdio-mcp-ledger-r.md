# BUG-0028: run_windows実行中にstdio MCP接続が切れるとledgerのrunが永久にrunningで残り、同run_idの再実行・cancel_runとも効かない

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 752215a

## 症状

- (未記入)

## 再現手順

Phase I screen-vscode-fill-save-001をclaude -p経由で実行(artifacts/phase-i/vscode-fill-x5/finitact trial1)。pickでfill候補を指定した3回目のrun_windowsで'STDIO connection closed (cleanly)'、新serverでは同run_idが'already running or stopped before a safe result'、get_run_journalはstatus running・events空のまま、cancel_runはcancellation_requested:trueだが変化なし。接続断の原因は未特定(MCP直結の再現3回では断は起きず)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 89897f1): ledgerに残るrunning行は別process由来で所有者が居ないのに、journalはrunning・cancelはtrueを返し続けた。読込時にinterruptedとし、journalはstatus interrupted+新run_idでの観測を促すevent、cancelはfalse、再実行は同文で拒否。接続断(server無言終了)の原因は未特定のため、faulthandler・例外tracebackを~/.local/state/finitact/mcp-server-fault.logへ、run errorと実行操作をmcp-server.logへ記録し、子processのstdinをDEVNULLにした
