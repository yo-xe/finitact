# BUG-0048: Start clickの後、SearchHost(検索)がforeground・全画面のスタートCoreWindowがhit rootになり、どちらをtargetにしてもforeground/hit判定で拒否される

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 4f0a5b2

## 症状

- (未記入)

## 再現手順

E2E-10 Finitact N=3(artifacts/e2e-live/e2e10-n3-finitact-bug47-20260929) 試行1・3: 検索target→point is covered by スタート、スタートtarget→foreground stayed on 検索。対処案: Start/Search等のshell surface同士はforeground許可集合に互いを含める(windows_screen_grounded の foreground_hwnds 既定値)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 992f497): Start/Search窓を互いのforeground・hit許可集合へ加えた。live: Start click後SearchHost前面でもStart再clickが3/3 confirmed(artifacts/e2e-live/e2e10-shell-repair-20260929/live3.jsonl)
