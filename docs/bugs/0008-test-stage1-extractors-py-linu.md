# BUG-0008: test_stage1_extractors.pyがLinux専用フォントパスをハードコードしておりWindows-nativeで収集失敗する

- 報告日: 2026-09-22 / 状態: 修正済み
- 重大度: 低 / 発生コミット: 8c219ff

## 症状

- (未記入)

## 再現手順

galleria実機のWindows-native Pythonでpytestを実行すると、tests/test_stage1_extractors.py:6の_FONT = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 24)がOSError: cannot open resourceでcollectionごと失敗する(Windowsにそのパスが存在しないため)。他78件はWindows-nativeで成功(tesseract CLI依存1件を除く)。

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-23 修正済み(コミット 3f5b963): Windows専用の代替パス欠如が原因。候補パスリスト(Linux/Windows/macOS)から探す方式へ直し、Windows-nativeでもtest_stage1_extractors.pyがcollection可能になった(コミット3f5b963)。
- 回帰テスト: tests/test_stage1_extractors.py
