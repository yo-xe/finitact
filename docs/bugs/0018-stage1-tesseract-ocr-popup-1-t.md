# BUG-0018: Stage1のtesseract OCRが小さいpopupの文字を等倍で1語も返さない(Tk 216x199 popupの720P/FULL HD/titleすべて0件。2-3倍拡大で720Pのみ、FULL HDはpsm 11でも不検出)。併せてextractorの引数順が tsv -l eng でありtesseractは-l/engをconfig fileとして読む(既定engのため顕在化せず)

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 中 / 発生コミット: af5c0eb

## 症状

- (未記入)

## 再現手順

scripts/check_windows_mcp_fgh_two_action_live.py をWindows-nativeで実行→2操作目でprovider_blocked(popup候補17件すべてboxed_region)。単体: popup_frame.pngへ tesseract img stdout -l eng tsv

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-23 修正済み(コミット 296f178): tesseractは216x199のpopupで等倍だと0語(太枠内の太字16px)。長辺600px未満のframeをgrayscale+整数倍拡大(最大4倍)してOCRし、rectを外向き丸めで逆写像。-l engをtsv config前へ移した。全window captureの較正は不変、F/G/H縦断liveはverified_success
