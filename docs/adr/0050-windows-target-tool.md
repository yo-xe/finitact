# ADR-0050: Windowsのtargetを窓タイトルでも受け外側向けtool定義から評価専用引数を外す

- 日付: 2026-09-30
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

統一条件の少数回live(`docs/plans/public-repo.md`のu1)で、promptからtarget_idを外すと外側agentが毎回`list_windows`を
呼び、1 runで終わるcaseでも固定費が約13.6k→18.7k tokenに増えた。tool定義は7本で約13.0k字あり毎turn読まれ、
その一部は評価scriptしか使わない引数の説明だった。Tk dragの負けは、dragが成立していたのに結果が`unverified`だけで、
外側がobserveで切れたlabelを見てBLOCKEDと報告したため。方針案はyo-xe了承済み(同計画「yo-xe指示」)。

## 決定

1. `run_windows`の`target_id`・`drop_target_id`と`observe_window`の`target_id`は、窓タイトル(完全一致優先、
   一意な部分一致)も受ける。`window:`/`uia:`/`screen:`形式はそのまま通す。曖昧・保護窓は候補付きで拒否し推測しない
   (`windows_inventory.resolve_window`)。ADR-0040の`window:`一本化はそのまま、入口を広げるだけ。
2. 公開schemaから全`title`と、`run_windows`の`click_label_constraints`・`selection_policies`・
   `decision_cache_allowed`・`exclusive_environment_ref`を外す。引数modelには残し、既存live scriptは動く。
   descriptionからADR番号・既定値の列挙・評価専用引数の説明を削る。7本で12,975→10,365字。
3. Windows経路で、outcomeが`unverified`かつmutationが`confirmed`のgoalに`screen_changes {gone, new}`
   (goal開始・終了のobservationのlabel差分、各6件、edge contour除外、drag開始候補は元のlabel)を付ける。

## 検討した代替案(没案)

- browser用とWindows用のMCP serverを分ける: 定義は約4.5k字減るが、両系比較の条件と登録手順が変わる。今回は見送り。
- promptへtarget_idを戻す: 平等test条件(同計画)に反する。
- Tk dragの結果説明をdescriptionへ書き足す: 説明文調整であり構造で直さない方針に反する。

## 影響

- 外側agentは窓タイトルだけで`run_windows`を呼べ、`list_windows`往復が不要になる。
- `GoalResult`に`screen_changes`が増える(Noneなら出力しない)。browser経路はfinal_stateがあるので付けない。
- Tk drag fixtureの心拍は別threadで書く(Tk threadのdrag中停止で判定不能になったため)。

<!-- 現況 -->
2026-09-30: 採択。実装済み、live確認は`docs/plans/public-repo.md`の手順3。
<!-- /現況 -->
