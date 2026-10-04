# BUG-0014: Windows mutex owner crash is invisible (no WAIT_ABANDONED) when no other process holds a handle: named mutex is destroyed with its last handle, so the next run creates a fresh mutex and proceeds without blocking the environment

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 6970ef7

## 症状

- (未記入)

## 再現手順

scripts/check_windows_mcp_lease_abandoned_live.py without the observer handle (_open_observer_handle): holder acquires and os._exit(0); run 1 gets WAIT_OBJECT_0 and reaches delivery-target verification instead of 'interaction lease was abandoned' (galleria 2026-09-23)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-23 修正済み(コミット d1d84de): 原因: named mutexは最後のhandleと共に消え、owner crash時に他handleが無いとWAIT_ABANDONEDが観測されない。対処: ADR-0013。processが名前ごとにhandleを生存期間中保持し、MCP serverはWindows起動時に開く。galleriaでobserver handle無しのprobe成立。Finitact不在中のcrashは検知不能(限界)
