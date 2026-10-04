# ADR-0026: screen fillの入力値をgoal単位のfill_valuesで明示する

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code(反証相談`docs/consult/20260925-0857-20260925-scroll-fill-structural-topic.md`)

## 背景

`screen-vscode-fill-save-001`はFinitact 3/5。失敗2本は外側agentがgoal文の値からバッククォートを落とし、
text helperが`value = "..."`の`...`だけを値と読んだ。値の正本が自由文にある限り、helperの再解釈で壊れる。

## 決定

- Windows runに任意`fill_values`(goal ID→入力文字列)を追加する。指定goalのfillはtext helperを呼ばず、その値を
  そのまま配送する。未指定goalは従来どおりhelperを使う。
- goal文と食い違っても構造化値を正本とする。空文字・2000字超・未知goal IDは入力エラー。
- 入力欄の選択はproviderに残す。異なる値を複数欄へ入れる時はgoalを分ける。
- 判断providerにも`DecisionRequest.goal_inputs`の`fill_value`として同じ値を渡す(untrusted_contextとは別)。
  decision cacheのkeyには指定時だけ含める。
- `provider_uncertain`後のpickは元runと同じ`fill_value`だけを受け入れる。

## 検討した代替案(没案)

- helperでgoal文と構造化値の整合を判定して不一致を拒否: 除きたい自由文の値解釈を整合性確認として再導入する。
- provider・helper向けの規則文追記: 文言調整はブラックボックス化するため採らない(2026-09-24のyo-xe判断)。
- 配送値だけ差し替えproviderには渡さない: goal文の値を期待するproviderが再fillやblockedを選ぶ経路が残る。

## 影響

保証するのは「渡された値を再解釈せず配送する」ことだけで、外側agentの誤転記は防げない。評価caseは
manifestの`fill_value`でopt-inする(`finitact/outer_agent.py`)。

<!-- 現況 -->
2026-09-25: 採択。live再比較でFinitact 5/5(`phase-i-comparison.md`)。
<!-- /現況 -->
