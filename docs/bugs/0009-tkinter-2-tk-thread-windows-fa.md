# BUG-0009: tkinterで2回目のTk()をthread再生成すると実機Windowsでfatal exception

- 報告日: 2026-09-22 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 296891d

## 症状

- (未記入)

## 再現手順

AutomationIndicator初期設計(thread内でtkinter.Tk()を生成しmainloop、hide()でdestroy、再show()で新threadを起動)をgalleria実機のWindows-native Pythonでpytest実行すると、2回目のshow()でTclインタプリタ生成時にWindows fatal exception code 0x80000003(STATUS_BREAKPOINT)でプロセスごと落ちた。WSL(WSLg経由のX11 display)では同じテストが問題なく通った(liveでしか出ない不具合)。

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-22 修正済み(コミット 296891d): thread内でTk()を使い回す設計をやめ、show()ごとに独立subprocessでtkinter windowを起動する設計に変更。プロセス分離によりTcl状態の再利用が発生しなくなり、繰り返しshow/hideがgalleria実機でlive成立することを確認した。
