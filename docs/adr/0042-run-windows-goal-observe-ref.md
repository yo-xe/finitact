# ADR-0042: run_windowsのgoalにobserve refを置く

- 日付: 2026-09-27
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

ADR-0041の`pick {observe_id, ref}`をE2E-03の外側agentが3試行とも使わず、observe→goal文のrun→uncertain→run由来pickの
往復が残った(E2E-I45)。説明文の調整は避ける(yo-xe)。外側agentが毎回書くのはgoalなので、入口をそこへ移す(Q-0001で選択)。

## 決定

- `run_windows`のgoalに`ref`欄を置く(最初のgoalだけ)。対象窓の最新の`observe_window`観測のrefとして解決し、
  以降はADR-0041の観測pickと同じ(fill値の有無でfill/click、TTL 60秒、run開始で失効、`fresh()`で同定)。
- 観測idは外側agentに持たせない。窓ごと最新1件の保持なので、target_idだけで一意に決まる。fingerprintには
  `latest_observation`として入るため、同一run_idの再送はreplayのまま。
- 2番目以降のgoalの`ref`、`ref`と`pick`の併用は入力検証で拒む。観測が無ければ配送せずblocked。
- `pick {observe_id, ref}`は互換のため残す。browserのgoalには`ref`を置かない。

## 検討した代替案(没案)

- 専用tool(`act_window {observe_id, ref}`): 見えるがtoolが増え、goal型runの達成判定・outcome語彙と別の口になる。
- (3)の打ち切り: 往復1回(約5〜10秒)が残る。

## 影響

- E2E-03 3/3・23.2〜23.7秒・外側68.7k〜71.6k token・run_windows 2回(E2E-I45の24.9〜35.9秒・82k〜94k・3〜4回)。
  6 runすべてで`ref`が使われた。
- 1試行はpopup候補の直接click後にJevがDONEを出さず「Email」を2回clickしてuncertain(値は入ったまま)。
  click後のDONE判断の既知の壁(BUG-0023)で、この決定の範囲外。

<!-- 現況 -->
2026-09-27: 採択。実装済み、E2E-03で外側agentが使用。
<!-- /現況 -->
