# BUG-0055: 空文字fillでSet-Clipboardが null 例外となり、クリック後に環境がblockされる

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 992f497

## 症状

- (未記入)

## 再現手順

check_shell_fill_live.py 試行1、検索欄へfill text=''(live2.jsonl)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 992f497): 空文字fillはpasteを使わずCtrl+A→Deleteで消す。live 3/3で検索欄のclearがconfirmed(artifacts/e2e-live/e2e10-shell-repair-20260929/live3.jsonl)
