# BUG-0007: Windows-native Python(cp932既定ロケール)でfinitactをimportするとUnicodeDecodeErrorで起動しない

- 報告日: 2026-09-22 / 状態: 修正済み
- 重大度: 中 / 発生コミット: f4594f2

## 症状

- (未記入)

## 再現手順

galleria実機のWindows-native Python 3.12(pip venv)でfinitactパッケージをimportすると、finitact/browser.pyのPath(__file__).with_name('snapshot.js').read_text()がencoding未指定のためOS既定ロケール(日本語WindowsはCP932)でデコードしようとし、UTF-8のsnapshot.js中のマルチバイト列でUnicodeDecodeErrorが発生する。同型の問題がinspector.pyのpath.read_text()(.env読み込み)とstatic asset読み込みにもあった(計3箇所)。

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-22 修正済み(コミット f4594f2): browser.py/inspector.pyの3箇所のread_text()にencoding="utf-8"を明示。ADR-0008のWindows-native live検証中にgalleria実機で実際に再現・修正した。
