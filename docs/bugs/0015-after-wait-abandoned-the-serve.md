# BUG-0015: After WAIT_ABANDONED the server keeps ownership of the desktop mutex on the asyncio.to_thread pool thread (never released): other processes time out until server exit, and a later run with a different exclusive_environment_ref on the same pool thread could re-acquire recursively, bypassing the per-ref block

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 6970ef7

## 症状

- (未記入)

## 再現手順

scripts/check_windows_mcp_lease_abandoned_live.py: post_run_mutex_check_from_separate_process = 'unavailable: interaction lease acquisition timed out' (galleria 2026-09-23). Recursive bypass inferred from Win32 mutex recursion + windows_interaction_lease.py:149 raising without ReleaseMutex; not yet reproduced live

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-23 修正済み(コミット d1d84de): 原因: WAIT_ABANDONEDで得た所有権をpool threadが保持し、同threadの別refが再帰取得でref単位blockを素通りしうる。対処: ADR-0013。放棄をmutex名単位でprocess内stickyにし、以後のacquireはWaitせずLeaseAbandoned。所有権は意図的に保持(他processはserver終了までfail-closed)。galleriaで別refのrun 3拒否を確認
