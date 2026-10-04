# ADR-0007: Stage1 extractorの外部依存許容

- 日付: 2026-09-22
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

`ScreenGroundedAdapter`のStage1(候補提案)を`ConfiguredRegionExtractor`の手作業矩形configから
汎用化する(EXP-0001)には、pixelから根拠付き`VisualRegion`有限リストを作る手段が要る。既存の
finitact依存(`httpx`/`mcp`)はこの用途を持たない。OCR系(tesseract CLI呼び出しのみ、新規Python依存
不要)と矩形検出系(numpy連結成分)の2方式を試すことにしたが、後者はnumpyという新規外部依存を要する。

## 決定

`finitact`本体がStage1 extractor実装のために外部依存(numpy等の軽量な画像処理ライブラリ)を持つことを
許容する。ただし追加するのは各extractorの実装に真に必要なものに限り、フレームワーク的な依存
(重量級CVライブラリ・MLモデル一式)は個別に再検討する。tesseract自体はCLI呼び出し(subprocess)で
済ませ、Pythonバインディング(pytesseract)は追加しない(標準ライブラリのみで完結するため)。

## 検討した代替案(没案)

- **numpyを使わず純Pythonで連結成分を書く**: 依存を増やさずに済むが、pixel単位ループが低速化し
  実画面(フルHD等の大きいframe)には現実的でない。プロトタイプ段階でも可読性・速度でnumpyを選んだ。
- **矩形検出をOpenCVで行う**: より高機能・高速だが、依存が重く(ネイティブビルド・システムライブラリ)
  EXP-0001の目的(Stage1の契約が成立するかの検証)には過剰。将来、性能要件が明確になった時点で
  numpyから置き換える選択肢として残す。

## 影響

- `pyproject.toml`の`dependencies`に`numpy`が追加される。
- EXP-0001が`--reject`となった場合はこのADRも合わせて見直す(実装ごと巻き戻る可能性がある)。

<!-- 現況 -->
2026-09-22: 採択。EXP-0001でnumpyベースの`EdgeRectangleRegionExtractor`を実装済み。
<!-- /現況 -->
