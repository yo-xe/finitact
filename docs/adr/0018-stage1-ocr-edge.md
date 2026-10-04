# ADR-0018: Stage1はOCR枠に中心が入るedge候補を落とす

- 日付: 2026-09-24
- 状態: 採択
- 決定者: Claude Code(可逆な局所判断。yo-xeの見直しで覆してよい)

## 背景

screen観測の候補は約2/3がedge領域で、その過半は中心がOCR枠内にあり、同じ場所を別idで重ねていた。
Blender(351件)・Unity 2局面目(284件)はTypeSafeの1問上限253件を超え、予選込み3 requestを使っていた(BUG-0021)。
E3(`docs/plans/ocr-reading-region-experiment.md`)をEXP-0003として固定frameで測った。

## 決定

1. `CompositeRegionExtractor`に`supplementary`を足し、補助extractorの領域は中心が主extractorの領域(2px余白)に
   入る時に落とす。本番のscreen経路はOCRを主、edgeを補助にする。
2. 判定はframe単位(同一scope)で、goalに依存しない。

## 検討した代替案(没案)

- **goal条件付きの粗→細**(planの当初案): 粗段は字面照合かprovider requestを要する。字面照合はBUG-0023の弱点と同じで、
  request追加は速度目標に反する。重複除去だけで6局面すべてが253件以下になったため先に採った。
- **edgeを全廃**: icon(Blender 17件)の唯一の供給源で、pooled recallを下げる。

## 影響

- EXP-0003(6局面×5回、`artifacts/heat-selection/20260924-exp0003.jsonl`): 候補351→227、284→102、215→88。
  正答24/30(現行23/30)、1判断中央値776→322ms、request 70→30、input tokens約4〜6割減。全局面で正解候補が残る。
- unity-open #1で1/5、BLOCKEDでなく誤候補("0.44x")を選んだ。現行では同じ局面がBLOCKED 2/5。
  fail closedが誤操作へ変わる経路として、BUG-0023の方針(Q-0005)と合わせて扱う。
- pooled recallの較正(Phase G)は両extractorを主として測るので値は変わらない。

<!-- 現況 -->
2026-09-24: 採択。実装済み(`finitact/stage1_extractors.py`、`finitact/windows_screen_grounded.py`)。live smoke(2026-09-24、`artifacts/phase-i/smoke4*`): Blender 2 caseとも外部success、終了理由は`budget`から`provider_done`へ。
内部providerは3 request(workspace 71k・mode 69k tokens、比較run中央値45k・92k)、壁時計は同等(21s・35s)。
<!-- /現況 -->
