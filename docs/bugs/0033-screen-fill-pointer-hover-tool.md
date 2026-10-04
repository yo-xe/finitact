# BUG-0033: screen fillが前試行のpointer残留で出たhover tooltip(VSCode command centerの'Search — <file> - Visual Studio Code')を選び、tooltip位置(634,44)へ配送。clickでfocusが移らず本文がfill全置換された(completed/unverified)

- 報告日: 2026-09-25 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 30a96b5

## 症状

- (未記入)

## 再現手順

scripts/check_windows_mcp_screen_fill_panel_live.py search --trials 5 --frames <dir>: 直前試行が(432,18)へ配送して終わると次試行でtooltipが出る。2/5で発生、frame search-2.png(windows-checkout artifacts/phase-i/fill-panel-search-x5b)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- fill配送(click→Ctrl+A→貼付)後にcursorが配送点へ残り、VSCode command centerのhoverがwindow内に描画され続けた。
  5試行で「Search成功→次がtooltip誤選択」が交互に出た。clickがtooltipに当たってもfocusは本文のままで、Ctrl+A+貼付が本文へ届く。
- 相談`docs/consult/20260925-1028-*`: cursor近傍の候補除外は本物の入力欄を消すため不採用。click後に入力先を同定する汎用native信号は未確認
  (MSAA `OBJID_CARET`は未検証)。Tkはwin32 focusをtoplevelに固定し、system caretの位置もclick後0.6秒以内に更新されない(実測)。
- MSAA `OBJID_CARET`実測(`scripts/probe_msaa_caret.py`、2026-09-25): VSCode(system caret無し)・Tkとも取得でき、click後80msで確定、照会1ms未満。
  入力欄click→caretは領域内(Tk 12/12・VSCode 6/6)。focusが動かない非入力click(caption・tab title)→caret位置不変で領域外(Tk 6/6・VSCode 3/3)。
  入力UIを開くclick(Search・status bar)→caretが新入力へ移動(6/6)。caretの幅は点滅で1→0と変わるため位置だけ比べる。
  判定案: caretがclick前後で位置不変かつfill領域外ならCtrl+A・貼付を送らない(未実装)。

## 修正

- 2026-09-25 修正済み(コミット f3353d5): fill後cursorを配送前位置へ戻す(click系はhover依存のため除外)。VSCode search live 5/5可(windows-checkout artifacts/phase-i/fill-panel-search-cursor-x5b、前回3/5)
- 回帰テスト: test_fill_returns_the_pointer_but_click_leaves_it_on_the_target
- 2026-09-25 残る危険(非入力点へのfill)をMSAA caret門で修正: click前後のcaretが同位置かつfill rect外ならkeyを送らず`FillTargetRefused`(guardがMutationUncertain・環境block)。
  live `scripts/check_fill_caret_gate_live.py`: Tk caption 3/3拒否・隣entry不変、VSCode tab title 3/3拒否・本文不変(editor fillは各3/3可)。回帰: VSCode search 3/3、save fill可
- 回帰テスト: test_fill_checks_the_msaa_caret_around_the_click_and_refuses_keys_when_it_stayed_outside
