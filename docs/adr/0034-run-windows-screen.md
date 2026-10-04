# ADR-0034: run_windowsの既定値でscreen要求を短くする

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-01でFinitact経路の外側agentはモデル時間35.3秒・出力2,267 tokenで、windows-mcp(12.2秒・754 token)の約3倍だった
(E2E-I12)。出力の大半は非表示の思考で、`run_windows`の必須項目(run_id、allowed_operations、synthetic_input_allowed、
exclusive_environment_ref、goal id)を毎回組み立てていた。yo-xeは速度優先を指示した。

## 決定

- `run_windows`は`target_id`とgoalsだけで呼べる。省略時の値は次の通り。
  - run_id: `run-<ランダム>`。応答喪失後の再送で同じrunを再生したい呼び手は、従来通り自分で付ける。
  - goal id: `g1`, `g2`, …。
  - allowed_operations: screenは`click, fill, key, scroll`、UIAは`click, fill, toggle, select`。double_click等は明示する。
  - synthetic_input_allowed: screenは許可、UIAは不許可。exclusive_environment_refは許可時`finitact-default`。
- screenの既定許可でも、ADR-0009の3層(mutex・idle・indicator)とADR-0033の前面化後の検証は毎配送で行う。
  明示の`synthetic_input_allowed: false`は従来通り観測のみにする。

## 検討した代替案(没案)

- 合成入力の許可をrequestごとの明示のまま残す: screen pathは合成入力なしでは何もできず、許可の判断が毎回の思考になるだけ。
- server起動時の環境変数で許可する: 登録済みclientごとの設定になり、既定の挙動が環境で変わって再現しにくい。
- 全pointer操作を既定にする: regionごとの候補が増えprovider入力が膨らむ(ADR-0033)。

## 影響

- E2E-01(外側agentを中立cwdで起動する条件)でFinitact 29.5秒・外側49,798 token、windows-mcp 14.4秒・96,618 token。
- 既存の明示指定はそのまま同じ意味で通る。

<!-- 現況 -->
2026-09-25: 採択。実装済み。
<!-- /現況 -->
