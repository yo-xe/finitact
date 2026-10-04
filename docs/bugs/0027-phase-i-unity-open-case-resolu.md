# BUG-0027: Phase I評価のUnity open caseが開始時のresolution値を固定せず、前runの終了値(16:9 Aspect/Full HD)でFinitactの経路(provider_done/pick)と時間が変わる

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 低 / 発生コミット: b26d6fb

## 症状

- (未記入)

## 再現手順

rec-cache-unity-x5(無効run、select setupで16:9へreset後に中断)の直後にopen caseを実行すると全trialでprovider_uncertain→pick経路になり、Full HD開始時より約4秒遅い(rec-cache-unity-x5b)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット b26d6fb): open caseのsetupが表示値を揃えず、前runの終了値が16:9 Aspectだと全trialでprovider_uncertain→pick経路になった。setupで表示値がFull HDでなければ選び直してから開始する。16:9からの2 trialでprovider_done・15.6秒を確認(bug0027-open-x2)
