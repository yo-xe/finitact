# ADR-0016: Stage1の既定OCRをPP-OCRv6 medium+OpenVINOにする

- 日付: 2026-09-24
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

ADR-0015決定1・追記1で、Phase I前にStage1 OCRを品質(位置recall・label正確性・id安定性・速度)と
展開性で選ぶことにした。tesseract、Windows OCR、PP-OCRv5 mobile、PP-OCRv6 small/medium(onnxruntime/
OpenVINO)を同一11 capture・同一edge extractorで較正した(`docs/evaluations/windows-mcp-replacement/
ocr-engine-comparison.md`、候補調査は`ocr-engine-landscape.md`)。

## 決定

1. screen-grounded経路の既定OCRをRapidOCR 3.9系のPP-OCRv6 mediumにする
   (`default_ocr_extractor()`、edge extractorとのCompositeは維持)。
2. backendはOpenVINOが導入済みならOpenVINO、無ければONNX Runtime。OpenVINOはx64前提。
3. 依存はoptional extra `screen`(rapidocr、onnxruntime、x64のみopenvino)。modelは初回使用時に
   RapidOCRが取得する(det+rec約100MB)。tesseract・Windows OCR extractorは差し替え用に残す。
4. 実測根拠(11 capture計): OCR recall 230/270(v5 mobile 197)、label正確性183/199(同125)、
   時間は最大1.24s/枚でv5 mobile(1.27s)並み。

## 検討した代替案(没案)

- **PP-OCRv6 medium+ONNX Runtimeのみ**: 品質は同等だが最大3.05s/枚で、1操作ごとのStage1に重い。
- **PP-OCRv6 small+OpenVINO**: 最速(0.67s/枚)だがlabel正確性150/199でmediumとの差が大きい。
- **PP-OCRv5 mobile(現行較正の暫定推奨)**: recall・labelともv6 mediumに劣り、速度優位も無い。
- **Windows OCR**: 最速だがWindows専用・言語packがOS依存・label正確性84/199。
- **tesseract**: id安定性は最良だが外部binaryとPATH依存(K-4a09e29bd7c9)、label正確性107/199。

## 影響

- id安定性はtesseractより悪い(state pairで画素不変領域の候補が0〜5件入れ替わる)。groupingと
  候補ID安定化はPhase I前の作業として計画へ入れる。
- arm64/macOSではONNX Runtimeへ退避し約3倍遅い。展開時の速度要件はPhase J以降で再評価する。
- Windows-native live環境へ`screen` extraの導入とmodelの事前取得が要る。

<!-- 現況 -->
2026-09-24: 採択(yo-xeが較正結果を見て支持)。既定extractorを切替済み。grouping・ID安定化は未着手。
<!-- /現況 -->
