# BUG-0054: SearchHost前面でtaskbar clickが拒否される: Shell_TrayWndがshell_surface_peers対象外

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 992f497

## 症状

- (未記入)

## 再現手順

artifacts/e2e-live/e2e10-n3-finitact-bug52-20260929 試行2。検索を開いたままtaskbarを操作。explorer.exe Shell_TrayWndがStart/Searchのpeerでない。

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 992f497): explorer.exeのShell_TrayWndだけをshell peerに加えた(通常フォルダー窓・Progmanは除外)。live: SearchHost前面でもtaskbar操作が3/3 confirmed(artifacts/e2e-live/e2e10-shell-repair-20260929/live3.jsonl)
