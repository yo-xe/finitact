# BUG-0023: screen単一質問(候補約210件)でBLOCKEDが確率0.2前後の相対最多で勝ち、正解候補を実行しない

- 報告日: 2026-09-24 / 状態: 未修正
- 重大度: 中 / 発生コミット: 7ef5fd9

## 症状

- 253件以下の単一質問(全候補+DONE/BLOCKED)で、BLOCKEDが絶対確率の低い相対最多のまま選ばれ、操作しない。

## 再現手順

scripts/measure_heat_selection.py measure を artifacts/heat-selection/20260924-situations.json のunity-open #1へ5回: BLOCKED 4/5(BLOCKED 0.20 vs Full HD 0.17-0.19)。live smoke3(2026-09-24)でもunity 2件がprovider_blocked。同じ局面を2 chunk予選へ強制すると5/5正解(0.86)

## 該当箇所

- `finitact/model.py` `choose_observed_candidates`(253件以下の1質問経路)

## 原因

- 未確定。終端を分けた一括予選(案A)でunity-open #1が5/5正答へ変わるため、多数候補へ分散した確率と
  単一の終端の比較が寄与している可能性がある。unity-select #1("16:9 Aspect")は案Aでも5/5 BLOCKED。

- 分布の測定(2026-09-24、固定frame、各3回): 重複・雑音への分散ではない。受理集合のedge boxは確率0で、OCR候補58件だけに絞っても
  同じBLOCKED。unity-openは残りの確率が同じtoolbarの別control("0.44x" 0.10-0.16、"Display 1" 0.07-0.10)へ流れる。
  unity-selectはBLOCKED 0.39で、goalから"Full HD"を外すと0.19へ下がる(16:9 Aspectは0.10-0.15で並ぶだけ)。
  dropdownの印(affordance)を4候補へ与えると"Game"(view種別dropdown)を0.36-0.43で選ぶ。
  見立て: 判断器がgoalの語と画面の語を字面で結んでおり、「16:9 Aspectがresolution dropdownの表示値」というアプリ知識が無い。
  最大確率が0.2-0.4の不確実な状態を、BLOCKEDが「進めない」として受け止めている。

- 判断器差し替えと目標文の言い換え(2026-09-24、固定frame、ADR-0018の候補除去後、生データ`artifacts/provider-trials/`):
  | 判断器 | 正答(6局面) | Unity 2局面の選択 | 1判断 |
  |---|---|---|---|
  | Jev(EXP-0003、各5回) | 24/30 | BLOCKED主体 | 約0.3s |
  | qwen2.5:14b(Ollama、seed 42/7で同一) | 3/6 | 両方"Game"タブ(誤操作) | 12〜40s |
  | Laya 0.3.5(20件予選→決選) | 0/6 | BLOCKED | 0.1〜0.3s(全体) |
  Layaは選択肢全体で192 tokenのため88件超を直接受けられず、予選20件の中でも正解を選ばない。判断器を替えても
  Unityは解けず、qwenはBLOCKEDを誤操作へ変える。目標文を外側agentが画面の語へ言い換えると
  (`Click the Game View resolution dropdown, whose button currently shows "16:9 Aspect"...`)、Jev 10/10(確信度0.94〜0.96)、
  qwen 2/2で正答した。欠けているのは判断器の能力ではなく、goalの語と画面の語を結ぶアプリ知識。
- 対処方針: ADR-0019(確信度0.4未満は`provider_uncertain`で上位label5件を返し、外側agentが言い換える)。計測はADR-0019の影響節。

## 判断(2026-09-24、`docs/consult/20260924-1324-20260924-bug0023-topic.md`)

- 現状維持(D)でPhase Iへ進む。理由は「採用証拠が単一局面のみ」で、解決不能ではない。
- 案A(253件以下も終端なし一括予選→決選、+1 request/判断)が次の修正候補。固定frame 5回:
  unity-open #1 1/5→5/5、unity-select #1 0/5→0/5(誤操作なし)。UIA単操作が予算3を超えるため、
  採否はPhase I結果と合わせて決める。事前登録予算3/6は据え置く。

- 案Aは棄却(EXP-0010、2026-09-25): screen判断が1回3 requestとなり事前登録予算でclick後のDONEが入らない。
  live現行の初手uncertainは6/15で、残る主因はUnity select #1(5/5、アプリ知識の欠落。案Aでも直らない)。

## 修正

- (dev bug close で記入される)
