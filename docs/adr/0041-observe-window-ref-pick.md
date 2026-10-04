# ADR-0041: observe_windowの項目refを既存pickの入口で直接実行する

- 日付: 2026-09-27
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-03では外側agentが`observe_window`で対象の項目を既に見ているのに、run_windowsはJevに選び直させ、
uncertain→pickの往復(1試行あたり約5〜6秒)が残る。yo-xe承認の速度改善(3)。consult第3案
(`docs/consult/20260927-0206-20260927-outer-target-and-ref-topic.md`詳細6)とADR-0040「影響」に従う。

## 決定

- `observe_window`は各項目に`ref`を付け、`observe_id`を返す。runは`pick {run_id: observe_id, ref}`で受ける。
  goal型runのままで、最初の1手だけJev選択を省く(達成判定E・outcome語彙は不変)。
- refは項目→{操作: 候補id}。最初のgoalにfill値があればfill候補、無ければclick候補へ解決する。
  その操作が無い・`allowed_operations`外なら配送せずblocked。
- 保持は窓(target_id)ごと最新1件、60秒上限。同じ窓のrun開始(pickの有無を問わない)で失効し、pickは一回限り。
  process内のみで再起動後は見つからずblocked。
- 同定は既存pickと同じくadoptした観測に対する`fresh()`: UIA窓は窓全体のUIA意味を再読して比べ、
  変わっていれば配送せずblocked。adoptは観測の付帯状態(`RetainedScreen`)ごと引き継ぐ(b966e93)。
- targetは観測と同じ経路(`window:`既定のscreen)に限る。`synthetic_input_allowed=false`のuia経路からは使えない。

## 検討した代替案(没案)

- 効果だけを見る直接操作API(consult叩き台B本体): outcome語彙を別に要し(consult詳細4)、速度差はgoal文生成分のtokenだけ。
- 要素単位の再同定(runtime id必須+rectは移動確認): 窓全体の意味比較より緩く誤配送の余地が増える。窓全体比較で
  staleが頻発すると実測で分かった時に検討する。
- 保持を「次の観測まで」だけにする: 外側agentが長考すると古いrefが生き続ける(consult詳細5)。

## 影響

- `mcp_server.observe_window`の説明にpick手順と期限を書いた。E2E-03で往復の削減を計測する。
- `observe_window`は毎回`retain`する(frame参照の保持で、再captureはしない)。

## 追記1(2026-09-27)

- E2E-03で外側agentがobserve refを`run_id`欄に直前のrun_idで渡しblockedになった(E2E-I45)。`pick`は
  run由来`run_id`とobserve由来`observe_id`の別欄とし、どちらか一方だけを受ける。

<!-- 現況 -->
2026-09-27: 採択。実装済み。外側agentの入口はADR-0042のgoal `ref`。
<!-- /現況 -->
