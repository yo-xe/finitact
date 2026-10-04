# BUG-0063: C2 E2E-05のwindows-mcp試行中に、フォルダ名 batch-0928 がyo-xeのClaude Code端末(WSL)の入力欄へ混入した。評価中の外側agentの入力が操作者端末へ漏れる

- 報告日: 2026-09-30 / 状態: 未修正
- 重大度: 中 / 発生コミット: 8ef87a5

## 症状

- windows-mcp系の試行中、外側agentが打った`batch-0928`が操作者のClaude Code端末の入力欄へ入った。Finitact系は同条件で入力していない。

## 再現手順

2026-09-30 11:21〜11:25 run_e2e_live.py windows-mcp --scenario E2E-05 --trials 5。Typeは全試行でbatch-0928を打ち、1790734975は2回((1210,179)と(1240,179))。Finitactは入力していない(observe_windowのみ)。端末窓は(1125,234)-(1821,850)で座標とは一致せず、どのTypeが漏れたかは未特定(artifacts/e2e-live/c2-20260930-E2E-05-windows-mcp/*.turns.json)

## 該当箇所

- 比較対象windows-mcpの`Type`(Finitactのコードではない)。harnessは`scripts/run_e2e_live.py`。

## 原因

- 5試行中4試行で、`loc`付き`Type`(完了まで約2秒)が左窓への`Click`/`MultiSelect`/drag`Move`と同じassistantメッセージで並列に出され、windows-mcpはそれらを並行実行する。`Type`は打鍵時点の前面窓の確認をしないため、打鍵中に前面窓が変わると別窓へ入る。
- どの`Type`が端末へ届いたかは記録(stream・turns)からは特定できない。端末窓の矩形は各`loc`と重ならないため、前面窓の移り変わりを経た漏れと推定する。
- Finitactは配送前に対象窓の前面・idleを確かめる(ADR-0032)ため同じ経路を持たない。比較対象側の性質として`docs/report/`の安全性の観察に載せる。

## 修正

- (dev bug close で記入される)

## 追記(2026-09-30 18:45 再発)

- 公開用本計測C2のE2E-05 windows-mcp(18:41〜18:46、`artifacts/e2e-live/c2-20260930f-E2E-05-windows-mcp`)で、`batch-0928`が再びyo-xeのClaude Code入力欄へ入った(yo-xe目視)。
