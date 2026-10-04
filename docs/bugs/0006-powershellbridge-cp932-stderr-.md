# BUG-0006: PowerShellBridgeがCP932 stderrをUTF-8復号して本来のWindowsエラーを隠す

- 報告日: 2026-09-22 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 46e24eb

## 症状

- (未記入)

## 再現手順

WindowsPrintWindowCapture.capture()でPowerShell scriptが失敗するとsubprocess text=TrueのcommunicateがUnicodeDecodeErrorになる

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-22 修正済み(コミット 46e24eb): subprocessをbytes受信へ変更し、UTF-8/CP932/UTF-16LEを順に復号してPowerShell本来のエラーを保持した。CP932回帰テストを追加。
