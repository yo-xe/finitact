# ADR-0027: 制約labelの探索とclickをfind_and_clickでprovider無しに選ぶ

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code(反証相談`docs/consult/20260925-0857-20260925-scroll-fill-structural-topic.md`)

## 背景

制約付き`screen-tk-scroll-select-001`はFinitact 3/5。失敗2本は各scrollと唯一のTARGET ROW clickでJevがuncertainとなり、
外側pickの往復でrun_id 4個を使い切った。ADR-0024の制約は配送対象の限定であり、「今clickせよ」という指示ではない。

## 決定

- Windows runに任意`selection_policies`(goal ID→`find_and_click`)を追加する。同goalの`click_label_constraints`必須。
- 制約で一意なclick候補が見えればproviderを経ずclickする。見えず、scroll候補の塊が1つだけならproviderを経ずwheelする。
- 探索方向は単調: down→(downのwheel後に画面無変化)→up→(upで無変化)→未発見で`blocked`。候補の消失では方向転換しない
  (adapterは反対方向の移動で端を再開し、OCR欠落でも消える)。upへ移った後に復活したdownは使わない。
- scroll塊が0個・複数の時はその手だけ既存providerへ戻す。塊の対応付けや横scrollは扱わない。
- click 1回後はproviderに終了判断だけを求め、`done`以外なら再clickせず`blocked`で返す。
- 自動の手はprovider予算を消費せず、action_budget・deadlineで止まる。metricsに`policy_actions`を出す。
- pickは元runと同じpolicyだけを受け入れる。

## 検討した代替案(没案)

- `click_label_constraints`指定だけで自動配送: 許可対象の限定を実行順序の指示へ拡張し、「欄を埋めてから保存」のような
  goalで早すぎるclickを生む。
- 候補にdownがあればdown、なければup: 末尾→up→down復活→末尾の往復が成立し、画面が変わるので無変化停止も効かない。
- 一意clickだけ自動化しscrollは現行: scrollごとの外側往復が残り、長いlistで再びrun_id上限に当たる。
- run_id上限を増やす: 外側往復が増え、主指標(速度・外側token)と逆向き。

## 影響

保証するのは有限停止と「制約labelを1回だけclickする」ことで、探索完遂・対象不存在の証明・outcome成功は保証しない。
無限listや上方の対象はbudget切れになり得る。評価caseはmanifestの`selection_policy`でopt-inする。

<!-- 現況 -->
2026-09-25: 採択。live再比較でFinitact 5/5(`phase-i-comparison.md`)。
<!-- /現況 -->
