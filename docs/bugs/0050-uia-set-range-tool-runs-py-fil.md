# BUG-0050: UIA set_rangeが実行ループから到達不能: 既定操作・tool説明に無く、runs.pyはfill以外にfill_valuesの値を渡さない。設定の音量sliderを外側が設定できない

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 4f0a5b2

## 症状

- (未記入)

## 再現手順

E2E-10 Finitact N=3(artifacts/e2e-live/e2e10-n3-finitact-bug47-20260929) 試行2: uia経路でfill 30を指示→候補は検索ボックスのみでprovider_uncertain、drag/クリック/矢印キーも失敗。runs.py:1185

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 22fe948): 原因: set_rangeは既定操作・tool説明に無く、runs.pyはfill以外にfill_valuesを渡さず、観測もRangeValueの現在値を読まなかった。対処: uia経路でfill許可時にset_rangeを含め、fill_valuesの数値をRangeValue.SetValueへ渡す(数値以外はnot_attempted)。観測はRangeValueの現在値を載せる。live: 設定の出力音量47→30→47をadapterで確認
