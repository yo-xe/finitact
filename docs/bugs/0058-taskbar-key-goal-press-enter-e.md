# BUG-0058: taskbarへのkey goal 'Press Enter'が検索結果から設定を起動した後も達成を認識できずEnterを繰り返し、2回目以降が空の検索paneを可視・前面で再表示して設定を覆う。以降の設定へのclickは'foreground stayed on 検索'で拒否される

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 694b67b

## 症状

- (未記入)

## 再現手順

run_windows(Shell_TrayWnd, Click Start + fill 'Settings') → run_windows(Shell_TrayWnd, 'Press Enter to open Settings', key)。mcp-server.logでrun-fd3eece9ee05がkeyを3回配送、終了後に検索CoreWindow(131562)がvisible・cloaked=0・前面(probe_search_holder.py --observe、画面でも確認)。原因候補: achievement.launched_windowがkey操作を除外し、新しい前面窓(設定)を終端証拠にしない(89b65d3、ADR-0035)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット b5eb1ab): keyの終端証拠を『検索/スタートが前面の時のkeyで非shell窓が前面化』に置き(開いた窓名をfitへ渡す)、前面が検索/不明の間は最大3秒待って判定。live 4/4(scripts/check_taskbar_enter_launch_live.py)、誤起動(メモ帳)はfit=Falseで未達成
