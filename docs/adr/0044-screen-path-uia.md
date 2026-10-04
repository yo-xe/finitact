# ADR-0044: screen pathの候補源を窓単位からUIA被覆の差の領域単位へ

- 日付: 2026-09-27
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

ADR-0036以降、screen pathは窓ごとにUIAかOCRかを二者択一し、合わない箇所へ操作依存の例外を足した(ADR-0036追記2)。
clickだけのrunではVSCodeのeditor本文が候補に出ない。yo-xe承認の一般化方針(1)として、consult
`docs/consult/20260927-1357-*`は「UIA被覆の差をOCR」を推した。ただしconsultの被覆集合(Text/Value付きDocumentを含む)は
live probeで反証された: ChromiumのルートDocumentが窓の98%を覆い、VSCodeのOCRが0%になる(editor喪失)。

probe(`scripts/probe_uia_opaque.py`・`scripts/probe_region_ocr.py`、live、2026-09-27)。被覆=本番readerの画面内・名前付き要素、
24px未満の帯を開演算で除き、空白tileを除いたmask済みcropを1回OCR:

| 窓 | 全面OCR | 領域OCR |
|---|---|---|
| Brave TypeSafe | 1484ms・23 region | 281ms・6(縦線の誤読"I") |
| Brave Home | 1375ms・121 | 172ms・1(バナー画像内の"TYPESAFE AI") |
| VSCode | 531ms・30 | 218ms・6(editor行`OLDVALUE`等を保持) |

## 決定

- UIAが答えた窓では、候補源=UIA要素 ∪ 未被覆領域のOCR。被覆=画面内の名前付きUIA要素(reader出力、offscreenは除く)の矩形。
  DocumentやPaneの矩形は被覆に数えない。ControlTypeや子孫の有無で不透明を判定しない。
- 未被覆は4px格子で開演算(24px)し、32px tileの縁画素が少ない空白tileを除く。残りの外接矩形を、被覆・空白部を中央値色で
  埋めたcropとして既存extractorへ渡す(画素キャッシュがそのまま効く)。OCR regionの中心がUIA矩形内なら捨てる(UIA優先)。
- 鮮度・再同定は候補の源ごと: UIA候補はUIA再読、OCR候補は領域OCRの再抽出とrect一致。
- 達成判定Eの証拠は、その観測の全源(UIA名・欄の値・領域OCR)で組む。全面OCR窓で出た候補だけは従来通り全面OCRで比べる
  (前後で源の組成を揃えるため、ADR-0036追記2の規則を維持)。
- 例外(a)は残す: fill許可かつeditable 0の窓は全面OCR。VSCodeのSearchはUIA上ボタンでeditable根拠が無く、OCRのfill panelが
  「clickして入力」を担う。例外(b)(drag時は全面OCR)も端点再同定の移行まで残す。
- UIAが答えない窓は従来の全面OCR。

## 検討した代替案(没案)

- consult案の被覆にText/Value付きDocumentを含める: ChromiumのルートDocumentが窓全体を覆い、VSCode editorが消える(probe)。
- 操作子孫の無い容器を不透明とする: Chromiumの`Intermediate D3D Window`が全面不透明と誤判定される(probe)。
- 窓単位のまま例外を一般化・常時全面OCR併用: consult 5節(速度が戻る)。
- 被覆をTextPatternの可視範囲矩形で取る: 未実測。本決定で不足が出たら検討する。

## 影響

- Brave等UIA窓のobserveに約0.2〜0.3秒の領域OCRが加わる(同一画素はキャッシュ)。E2E-03の1.4〜1.6秒/runを維持できるか回帰で見る。
- 回帰指標はconsult 6節(目標候補の再現・OCR面積比・observe p50・Eの誤達成0と未達成・重複件数)。
- 空白判定の閾値と誤読"I"の扱いは回帰の結果で詰める。

## 追記1(2026-09-27): 例外(b)を撤去

dragもUIA窓ではUIA要素∪未被覆OCRから始点・終点を出し、実行前の再同定も同じ源(`_regions_now`)で行う。
終点候補は始点を出した観測の領域をそのまま使う(再OCRしない)。live: Phase I `screen-tk-drag-drop-001`(UIA 4要素・
未被覆比0.468)で外側agent経由3/3到達(1件はTkの状態writer停止でoracleがundetermined、遷移記録では7.1秒で到達)、
1 call・9.9〜49.9秒。Windows側`artifacts/phase-i/adr0044-regress/`。

## 追記2(2026-09-27): Unityの回帰

Unity Editor(UIA 14要素・未被覆比0.19)で目標候補(`Full HD`・`16:9`)を再現、UIA/OCR重複0、warm観測0.125秒
(`region-source-adr0044-results.json`)。外側agent経由でopen 3/3・14.7〜20.5秒、select 3/3・26.0〜36.7秒
(Phase I時 16.8秒・28.2秒と同水準、`unity-adr0044-results.jsonl`)。

<!-- 現況 -->
2026-09-27: 採択。実装済み。例外(b)は追記1で撤去。live回帰は全case完了(Unityは追記2)。
<!-- /現況 -->
