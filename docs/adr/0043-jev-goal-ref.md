# ADR-0043: Jev省略の入口をgoal refへ一本化する

- 日付: 2026-09-27
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

判断器を省いて候補を直接実行する入口が3つあった: uncertain run由来の`pick {run_id, ref}`(ADR-0022)、
`pick {observe_id, ref}`(ADR-0041)、最初のgoalの`ref`(ADR-0042)。外側agentが実際に使うのはgoalの`ref`だけで
(E2E-I45)、個別最適化の一般化方針(yo-xe承認、2026-09-27、STATUS)の(2)で入口を1つにする。

## 決定

- 入口は最初のgoalの`ref`だけ。`pick`入力と`observe_id`を廃止し、`run_windows`は未知の欄を拒む(旧pickが
  判断器任せの通常runとして黙って走るのを防ぐ)。
- uncertain runが終えた観測(`screen_candidates`を出した観測)を、observe_windowと同じく窓の最新観測として保持する。
  refの規則(窓ごと最新1件・TTL 60秒・次のrun開始で失効・`fresh()`で同定)は両者共通。
- 操作の解決: fill値があればfill、無ければclick。項目がその操作を持たず操作が1つだけなら(run候補は1候補=1操作)
  その操作をとる。許可外は`blocked`。
- run由来pickが課していた「goal・click制約・fill値・selection policyが元runと同一」の照合は廃止。goalを言い換えて
  refを付けてよい。同定は窓の観測と`fresh()`が担う。

## 検討した代替案(没案)

- run由来pickをrun_id指定のまま残す: 入口が2つ残り、外側agentはrun_idの取り違えを起こした(ADR-0041背景)。
- observe項目にも「操作1つならそれ」を当てない: run候補と規則が分かれる。click-onlyの項目をfill goalで指すと
  clickして判断器が続けてfillするのはrun由来pick(ADR-0022)と同じ振る舞い。

## 影響

- `CandidatePick`は`ref`だけ。uncertain runのフレーム保持は窓ごと最新1件になり、別窓のuncertain runを跨いだ保持(旧4件)は無い。
- observe項目でclickしか持たない項目をfill goalで指すと、以前は`blocked`、今はclick後に判断器がfillへ進む。
- 外側agentの計測ハーネス(`outer_agent.py`)と`scripts/check_windows_mcp_screen_fill_panel_live.py`・
  `measure_direct_pick.py`をgoal `ref`へ移した。live再計測(回帰セット)は方針(4)で一括して行う。

<!-- 現況 -->
2026-09-27: 採択。実装済み、単体テストのみ(live未計測)。
<!-- /現況 -->
