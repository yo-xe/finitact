# BUG-0046: run_windows: Win11メモ帳の本文(UIA Document、RichEditD2DPT)が入力候補に出ず、外側はOCR由来の状態バー文字列(0 文字・テキスト)をfill候補と誤認して入力できない。1回はその候補がChrome窓に覆われているとして配送検証で止まった

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 中 / 発生コミット: d635cd2

## 症状

- 本文への入力候補が無く、外側は状態バーの「0 文字」「テキスト」をfill候補として選んだ。

## 再現手順

run_e2e_live.py finitact --scenario E2E-09(artifacts/e2e-live/e2e09-n3-finitact-20260929、1/3)。成功した1回も本文への入力はprovider_done・unverified

## 該当箇所

- `finitact/windows_uia_reader.py` `ComUiaReader._read`(対象ControlType表`CLICK_ROLES`)

## 原因

- 本文は`ControlType.Document`(50030、`RichEditD2DPT`、ValuePattern書込可)で、`CLICK_ROLES`に無いため読み捨てていた。
  editableなUIA要素が0件になり、fill候補はOCR由来の全文字列(状態バー含む)へ落ちていた。

## 修正

- 2026-09-29 修正済み(コミット 34fc57c): 本文のUIA Document(RichEditD2DPT)がCLICK_ROLES外で読み捨てられ、editable 0件でOCR由来fill(状態バー文字列)へ落ちていた。書込可ValuePatternのDocumentをrole documentのeditableとして読む(読取専用のChromeページは除外、IsDataValidForFormは見ない)。E2E-09 Finitact 3/3
