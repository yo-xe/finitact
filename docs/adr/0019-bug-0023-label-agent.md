# ADR-0019: BUG-0023: 判断器が不確実な時は上位labelを返し外側agentに言い換えさせる

- 日付: 2026-09-24
- 状態: 採択
- 決定者: yo-xe + Claude Code(Q-0005。yo-xeは「計測で仮説と食い違いが無ければ推奨で進めてよい」と回答)

## 背景

Unityのscreen caseは、正解候補がprovider入力にあってもJevがBLOCKEDを返す(BUG-0023)。判断器はgoalの語と画面の語を
字面で結び、アプリ知識が無い。外側agentはFinitact経由では画面を見ないので、画面の語を得るにはFinitactの観測を返す必要がある。

## 決定

1. 判断の確信度が閾値0.4未満の時は、非終端の選択でも低確信のBLOCKEDでも操作せず、goalを新しい終了理由
   `provider_uncertain`で終える。高確信のBLOCKED・DONEは今のまま扱う。
2. `provider_uncertain`の結果には、確率上位5件の非終端候補のlabel(画面由来のuntrusted data)を付ける。
3. 外側agentはそのlabelでgoalを言い換え、run_windowsを呼び直す。判断・再同定・fail closedはFinitactに残す。
4. tool説明で画面の語を推測させる誘導(b1)は入れない。

## 検討した代替案(没案)

- **b1 tool説明で画面の語を推測させる**: 16/18。Unityでは正答でも確信度が0.16〜0.49のまま。試走では外側agentが
  推測した"Display 1"へJevが0.72で誤って寄った。fail closedを誤操作へ変える経路になる。
- **a2 上位候補のidを外側agentが直接指定**: 18/19。試走でUnity select#1に"Game"を選んだ。新toolと同一観測の
  再同定契約が要るのに、正答率はa1以下。
- **候補リスト全件を常に外側agentへ渡す**: 手順ごとに外側の往復(約3秒)とtokenが増え、windows-mcpの方式へ近づく。
  速度第一の方針に反する。
- b2(アプリ別用語集)とb3(成功runの記録)は、保守を人手に頼るか、初回を救えない。

## 影響

- 計測(`scripts/measure_q0005.py`、`artifacts/q0005/20260924-measure.jsonl`、6局面×Jev 3回・外側sonnet 3標本)。
  a0(現行)はUnity select#1で0/3、open#1で2/3(確信度0.23〜0.26)。a1は18/18、試走を含め19/19。確信度は0.64〜0.98で、
  Unityは0.90〜0.95。正解は全局面で上位5件内(順位3以内)。外側の1往復は2.5〜6.4s。
- 閾値0.4の根拠: EXP-0003とこの計測で、Unityの確信度は最大0.32、Blenderの正答は最低0.46。余白が小さいので、
  他のcaseを足したら見直す。ADR-0018の影響に挙げた低確信の誤選択("0.44x" 0.22)も、この閾値で操作前に止まる。
- 呼び出しが1往復増えるのはUnity型の局面だけ。UIA・Blenderの経路は変わらない。

<!-- 現況 -->
2026-09-24: 採択・実装済み(`finitact/runs.py`の`UNCERTAIN_CONFIDENCE`、`GoalResult.screen_labels`、outer_agentは
`-2`〜`-4`のrun_idで呼び直し)。低確信の`DONE`は対象外(verifierが判定する)。live再runは未実施。
<!-- /現況 -->
