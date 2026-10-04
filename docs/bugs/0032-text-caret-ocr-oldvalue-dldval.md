# BUG-0032: 撮影時にtext caretが点灯しているとその行のOCRが化ける(OLDVALUE→DLDVALUE)。_equivalent_framesは比較判定のみでラベル読取には未対処

- 報告日: 2026-09-25 / 状態: 対応不要
- 重大度: 中 / 発生コミット: b7584a0

## 症状

- (未記入)

## 再現手順

VSCodeで本文先頭にcaretがある状態でscreen observe。Windows側artifacts/phase-i/vscode-fill-values-x5 trial 1・vscode-fill-fix4 trial 3で本文候補ラベルがDLDVALUE

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- 観測は撮影1枚をそのままOCRする。`_equivalent_frames`(`finitact/screen_grounded_adapter.py:842`)は2枚の比較判定だけでラベル読取に効かない。
- `scripts/probe_screen_caret.py`(2026-09-25 live): 使い捨てVSCodeで0.23秒間隔12枚中5枚がDLDVALUE。GetGUIThreadInfoのcaretは
  hwnd 0・rect 0(Chromiumはsystem caretを出さない)ため、caret位置は画素から求めるしかない。
- 同probe再実行(2026-09-25 09:23): 点灯8枚は`DLDVALUE` conf 0.846、消灯4枚は`OLDVALUE` conf 1.0。
  消灯frameとの差分箱は常に2x19px(x=129, y=91)。

## 修正

- 2026-09-25 対応不要(コミット 2917682): 撮り直し案(ADR-0028)は誤読を消したが時間中央値20.0→27.6秒で速度第一に反し廃止。置換表は照合先の正解語が無く適用点が無い(labelはJevへ素通し、達成判定Eのlabel_diffは編集距離1を同一視)。BUG-0032起因の失敗は未観測で、初手uncertainの原因でもない(ADR-0028現況)。単一frameのcaret除去は上下にはみ出さない行で検出できず、効用が未観測のため着手しない
