# BUG-0042: run_browserがCDP対象のbrowser不在時にchrome-not-runningで失敗するまで約120秒かかる(E2E-04 trial1で外側がrun_browserを選び120秒を失った)

- 報告日: 2026-09-28 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 9cc1320

## 症状

- (未記入)

## 再現手順

Windows側MCP serverでBU_CDP_URL未設定・Chrome未起動のままrun_browser(start_url=https://www.google.com)を呼ぶ。artifacts/e2e-live/e2e04-n3-ocrwarm-20260928/e2e04-finitact-1790585261.stream.jsonl t=23.5s→144.2s、run-5e8a807e4c4c

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-28 修正済み(コミット 9c15021): browser不在時にbrowser_harnessのensure_daemonがChrome自動起動と再試行(60秒枠)へ入り約120秒後に失敗していた。finitact.browser.start_harnessでdaemon未起動時に先行確認(BU_CDP_URLは/json/versionを2秒timeout、ローカル探索はsupported_browser_running)し即chrome-not-runningで失敗させる。Windows live: 未到達CDP URL 2.03秒、browser不在ローカル0.19秒
