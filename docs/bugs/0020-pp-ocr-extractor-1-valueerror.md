# BUG-0020: PP-OCR extractorが文字を1つも検出しない画像でValueErrorを投げる

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 500beff

## 症状

- (未記入)

## 再現手順

RapidOCRが空検出時に番兵('',1.0,None)を返す画像(電卓captureの右中央タイル等)をPaddleOcrTextRegionExtractor.extractへ渡す

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 500beff): word_resultsの空検出番兵は入れ子でない平坦tupleで、行として展開して落ちていた。先頭要素が文字列の要素を番兵として読み飛ばす
