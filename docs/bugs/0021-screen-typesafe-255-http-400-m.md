# BUG-0021: screen観測の候補がTypeSafeの選択肢上限255を超えるとHTTP 400で停止し、MCP結果はRuntimeErrorとしか出ない

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 9b9d7ae

## 症状

- (未記入)

## 再現手順

Phase I runner: run_phase_i_comparison.py finitact --case screen-blender-workspace-001。Blender factory-startup画面で候補351件+DONE/BLOCKED=353、SystemOneが{"detail":"Too many choices. Must have at most 255 choices."}を返す

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 2b42f17): 候補+DONE/BLOCKEDが255超で1質問に載らない。253件超のみ連続chunkの予選(DONE/BLOCKEDなし)→勝者+DONE/BLOCKEDの決選に分け、全requestを既存attempt予算へ課金(consult 20260924-1304 案B)。判断品質は固定frame測定で未確認
