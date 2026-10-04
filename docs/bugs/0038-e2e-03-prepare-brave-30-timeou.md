# BUG-0038: E2E-03 prepareがBrave未起動時に起動プロセスの終了を待って30秒timeoutする

- 報告日: 2026-09-27 / 状態: 修正済み
- 重大度: 中 / 発生コミット: a202f3e

## 症状

- Brave未起動時、E2E-03のprepareが起動処理で30秒timeoutする。起動済みの場合は通る。

## 再現手順

Braveを未起動にしてscripts/run_e2e_live.pyのE2E-03 prepareを実行。brave.exe --incognito起動のsubprocess.runがブラウザ終了を待ちTimeoutExpiredになる

## 該当箇所

- `scripts/run_e2e_live.py`の`e2e03_prepare`

## 原因

- `subprocess.run`が最初に起動したBraveプロセスの終了を待っていた。

## 修正

- 2026-09-27: 起動を`subprocess.Popen`へ変更し、UIAで初期画面を確認する。10秒生存する代替プロセスでprepareが約1msで戻ることを確認。実Braveの未起動状態からの再現は未実施。
