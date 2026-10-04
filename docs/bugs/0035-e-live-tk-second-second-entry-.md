# BUG-0035: 達成判定Eのlive確認でtk-second(Second entryへfillしgoalはFirst)のrun中にmain python.exeがネイティブcrashし出力なく停止

- 報告日: 2026-09-25 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 6901f8a

## 症状

- (未記入)

## 再現手順

Windows側でpyt.bat scripts/check_achievement_e_live.py --values 1 --only tk。tk-firstは完了、tk-secondで2回目のdecide(unpicked_fill_labels出力)直後にmainが消えfaulthandler(dump_traceback_later)も無出力、Tk targetが孤児で残りpipeを保持して呼出側はtimeoutまで待つ。2/2再現。WERにpython.exe APPCRASH c0000005(ntdll+0x161914)とc000041dの記録あり(対応は未確認)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-25 修正済み(コミット 760aba5): win32_overlayはwindow classを初回overlayのWNDPROCで登録し、close時にそのtrampolineを解放していた。同一process内の2回目以降のoverlay(runごとにindicatorを作り直すため2 run目以降)はERROR_CLASS_ALREADY_EXISTSで解放済みWNDPROCを継承し、DestroyWindowで0xc000001d。class名ごとにWNDPROCをprocess寿命で保持するよう修正。faulthandler有効で原因特定、scripts/check_overlay_reuse_live.pyで旧版は1 round目で登録WNDPROC未保持・修正版は3/3保持、achievement E tk liveはtk-secondまで完走。常駐MCPserverも2 run目以降で同じ危険があった
