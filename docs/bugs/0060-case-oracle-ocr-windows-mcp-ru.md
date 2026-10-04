# BUG-0060: 電卓caseの外部oracle(OCR)がキー入力後の表示を読めず、windows-mcp系のキー入力runを判定不能として分母から外す(C1で6/10)

- 報告日: 2026-09-30 / 状態: 修正済み
- 重大度: 中 / 発生コミット: db49fa4

## 症状

- (未記入)

## 再現手順

電卓を起動し前面化してキー'7'を送る(Alt経由でも同じ)→表示にフォーカス枠が付き、tallest_ocr_lineがPP-OCR・Tesseractとも表示行を検出しない。クリック入力では読める。_probe/calc_key7.py

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-30 修正済み(コミット db49fa4): キー入力後の表示フォーカス枠でOCR両engineが表示を落としていた。電卓oracleをCalculatorResultsのUIA Name読み(Finitactのreaderと別の単一問い合わせ)へ替えた。live再計測で両系10/10・判定不能0
