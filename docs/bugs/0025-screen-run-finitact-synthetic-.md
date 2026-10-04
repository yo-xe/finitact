# BUG-0025: screen runでFinitact自身のsynthetic clickが入力idleを数え直させ、同一outer session内の次のrun_windowsがidle前提(30秒)未満で止まる

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 251d4d0

## 症状

- (未記入)

## 再現手順

run_phase_i_comparison.py finitact --case screen-unity-select-resolution-001 --trials 5(Windows側artifacts/phase-i/adr0020-unity-x5、trial 4): 2回目の呼び出しがclick後、4回目がidle 24.3s < 30.0sでblocked

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 25d0c92): 原因: 自runのSendInputが最終入力tickを進め、次runのidle確認(ADR-0012 B2の4項)で拒否された。対処: ADR-0021、process共有OwnInputLedgerでrun終了時tickが不変ならidleを前runの起点から測る
