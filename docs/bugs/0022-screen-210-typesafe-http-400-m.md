# BUG-0022: screen候補210件以上でTypeSafeがHTTP 400 max_tokens_exceeded(候補recordをstateとcriteriaへ二重送信)

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 378cc10

## 症状

- (未記入)

## 再現手順

scripts/measure_heat_selection.py measure --situations artifacts/heat-selection/20260924-situations.json: 210件で入力約41k tokens相当が400、二重送信を外すと20.9k tokensで200、253件でも24.9k tokensで200

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 378cc10): 候補recordをstateとcriteriaへ二重送信し入力が倍になっていた。stateから外し決選だけにラベル一覧を渡す。253件単一質問24.9k tokensで通過を確認
