# BUG-0026: provider_uncertainのscreen_labelsにedge候補名(boxed_region_WxH)が入り、外側agentの言い換えが無意味な語になる

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 251d4d0

## 症状

- (未記入)

## 再現手順

同run trial 4: 2回目以降のscreen_labelsが['16:9 Aspect','boxed_region_296x907',...]、外側が'boxed_region_296x907'をgoalへ書き込み

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 25d0c92): 原因: _likely_labelsがedge_contour候補の合成label(boxed_region_WxH)も返した。対処: source=edge_contourの候補を除外(EDGE_CONTOUR_SOURCEをcontractsへ)
