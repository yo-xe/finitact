# BUG-0077: Windows interaction lease: WAIT_ABANDONEDを受けた待機側が所有権を得たままReleaseMutexせず例外を投げるため、以後の全プロセスがabandonedを継承し run_windows が永続的にBLOCKED

- 報告日: 2026-10-04 / 状態: 未修正
- 重大度: 中 / 発生コミット: 391d846

## 症状

- (未記入)

## 再現手順

run_windows実行中のmcp_serverプロセスを強制終了→新しいプロセスでrun_windows→'interaction lease owner exited unexpectedly'。プロセスを替えても毎回再発(2026-10-04 E2E-04 F -r2 試行1-2)。手動でWaitForSingleObject→ReleaseMutexすると解消

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- (dev bug close で記入される)
