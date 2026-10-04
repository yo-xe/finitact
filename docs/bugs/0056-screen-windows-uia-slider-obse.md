# BUG-0056: 既定のscreen経路ではWindowsのUIA slider(設定の出力音量)が候補・observe項目に出ず、外側はつまみを座標でピンポイントにclick/dragするしかない

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: d1825ab

## 症状

- (未記入)

## 再現手順

Settingsのサウンド画面でobserve_window(window:)。UIAにSlider『出力音量の調節』(SystemSettings_Audio_Output_VolumeValue_Slider, rect 1394,420,200,32, RangeValue 0-100)があるのに86項目に無い。observeはallowed_operations=(click,fill)固定(windows_inventory.py:203)、set_rangeはuia経路だけに付く(mcp_server.py:_with_range)。E2E-10 Finitact 3/3でも音量goalがprovider_uncertainを繰り返し160〜290秒の主因(e2e10-n3-finitact-shell-20260929)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 063bc54): screen経路のUIA readerがSlider/RangeValueを読まずset_range語彙も無かった。readerでsliderを読み、set_rangeをUIA SetValue+読み戻しで配送(063bc54)。live E2E-10で音量goalが1 runで30へ到達・読み戻し一致(artifacts/e2e-live/e2e10-n3-finitact-{slider,cloaked,cloaked2}-20260929)
