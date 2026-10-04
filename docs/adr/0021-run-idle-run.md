# ADR-0021: 自runの入力だけが続いた時はidle起点を次runへ持ち越す

- 日付: 2026-09-24
- 状態: 採択
- 決定者: Claude Code(ADR-0012 B2の残余リスクを広げない範囲の変更。速度優先はyo-xeの既定方針)

## 背景

BUG-0025: screen runでFinitact自身のclickが`GetLastInputInfo`の最終入力tickを進め、同じouter sessionの次の
`run_windows`が30秒idle未満で`blocked`になる。ADR-0012 B2の4項「idle許可を次runへ持ち越さない」が原因。
外側agentの言い換え・複数goalは次runを数秒後に呼ぶので、Unity select caseのように2回以上clickすると必ず止まる。

## 決定

1. process共有の`OwnInputLedger`が、idle確認を通ったrunの終了時(lease close)の最終入力tickと、そのrunが
   確認したidle起点tickを記録する。
2. 次runのidle確認で最終入力tickが記録値と一致すれば、idleを記録済みの起点から測る。一致しなければ従来どおり
   最終入力tickから測る。
3. 記録はrun終了時に行う。SendInputのtick更新は非同期で、配送直後に読むと自分の入力を取り逃がす。

run間の入力は、人間でも別processのSendInputでも最終入力tickを変えるので検出できる。新たに見落とすのは
run実行中の人間入力だけで、これはB2で受容済みの残余リスクと同じ範囲である。

## 検討した代替案(没案)

- **30秒idleを待ってから配送する(B3と同型)**: 1手ごとに30秒以上増え、windows-mcpより速くする目的に反する。
- **B1(ADR-0012没案)をrun内に導入**: 人間入力の後に自分の入力が入ると見落とす問題は変わらず、本件の解決に不要。
- **複数goal・言い換えを1runに収める**: 外側agentの言い換えはrun終了後にしか起きない。

## 影響

- `finitact/windows_interaction_lease.py`(`OwnInputLedger`、`WindowsIdleTimePrecondition(ledger=)`)、
  `finitact/windows_screen_grounded.py`(process共有ledger)。
- idle不足で拒否されたrunは記録しない。lease解放失敗はADR-0009どおりenvironmentをblockする。

<!-- 現況 -->
2026-09-24: 採択・実装済み。live: `scripts/check_windows_mcp_two_action_live.py --split`(Tk、1操作×2run)で2run目は実idle 4.5秒・持ち越し63.5秒で通過しoutcome_verified。
<!-- /現況 -->
