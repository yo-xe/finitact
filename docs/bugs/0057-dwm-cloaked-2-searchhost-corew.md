# BUG-0057: 非表示(DWM cloaked=2)のSearchHost CoreWindowが前面を握ったままだと、taskbarのStart click・検索欄fillが'foreground stayed on ... 検索'でnot_attemptedになり、外側は不可視の検索窓を直接対象にして空振りする

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 063bc54

## 症状

- 検索を閉じた後も前面は`SearchHost.exe`の`Windows.UI.Core.CoreWindow`(DWMWA_CLOAKED=2)に残る(probeで確認)。
  Start clickは`delivery target verification failed: foreground stayed on window:131562:14356 ... '検索'`でnot_attempted。
- 外側はdetailに出たcloaked窓を直接targetにし、見えない検索欄へfill・Enterを送って空振り、BLOCKEDで終了(34.1秒、誤DONEなし)。

## 再現手順

E2E-10 N=3の試行3(artifacts/e2e-live/e2e10-n3-finitact-slider-20260929、stream 1790670353)。検索窓を閉じた後にSettingsを閉じると前面が cloaked の SearchHost に残る。その状態で run_windows(Shell_TrayWnd, 'Click the Start button')

## 該当箇所

- `finitact/windows_inventory.py`の`shell_surface_peers`(cloaked窓を除外)と`WindowsSendInputPointer._scope`

## 原因

- 前面許可の拡張(BUG-0048/0054)は可視のshell面だけを対象にし、cloakedで前面を握る検索窓を拒否していた。

## 修正

- 2026-09-29 修正済み(コミット 694b67b): cloaked/非表示の検索窓をpointer操作の前面許可へ加えた(694b67b)。E2E-10 N=3(e2e10-n3-finitact-hidden-20260929)3/3・96.7/66.7/58.6秒、非表示holderによる拒否0。残った拒否1件は検索paneが実際に可視で再表示されたもの(BUG-0058)で、拒否は正しい
