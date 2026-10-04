# ADR-0036: screen pathの候補源をCOM UIA第一にしOCRを補完と証拠に回す

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-01のFinitact内部時間の主因はOCRだった。desktopは全面約2〜4秒で壁紙の文字まで候補化し、Jevが予選と決勝の
2段になる。OCRの誤読(ト→卜、字間空白)と、入力欄と投稿の区別不能も残る(E2E-I14)。yo-xeはOCRの前提を疑うよう求めた。
COM IUIAutomationの実測はdesktop 32要素19〜24ms、Discord 309要素58msで、名前・role・rectが正確だった
(.NET版UIAではdesktopのicon一覧が見えない)。consult(`docs/consult/20260925-2134-consult-uia-first.md`)は修正付き採用。

## 決定

- screen pathの操作候補を、COM UIAの名前付き・画面内要素から第一に作る。配送はSendInputとADR-0009・0033の門のまま。
- UIA候補は画素の同一性で鮮度・再同定を代用しない。配送直前にUIAを読み直し、同じrole・名前・rectの要素が一意に
  あることを確かめる。画素キャッシュはOCR専用。
- fill候補は編集可能の根拠(Edit、または書込み可能なValuePattern)がある要素だけ。現在値は名前と別に渡す。
- UIAから操作候補が得られない窓・読取失敗・時間切れはOCRへ戻す。要素数の閾値でOCRを不要と判定しない。
- 達成判定E・ADR-0035の終端証拠は従来の画素・OCR証拠のまま。証拠用OCRは必要時に取る。
- UIA読取は専用スレッドでCOMを初期化し、親側の期限を付ける。期限切れの結果は捨てる(consultの推す常駐helper
  processは、ハングが実害になった時に移す)。

## 検討した代替案(没案)

- 差分領域だけOCR: OCRが要るアプリの高速化として残すが、誤読・ノイズ・入力欄の区別は解けない。
- 投機実行・並列化: 撮影→読取→判断が直列依存で、先読みの外れは誤操作になる。Jevの予選は既に並列。
- 要素数閾値でOCRを省く: 名前付きツールバーとcanvas本文の窓で本文候補がゼロになる(consult)。
- UIA候補を既存extractorへ差し替えるだけ: 画素一致で再抽出を省くfresh・resolve・cacheが古いUIA状態を新鮮と判定する(consult)。

## 影響

- `docs/plans/uia-first-screen.md`で段階導入する。最初はE2E-01の候補選択へ適用し、時間・Jev往復・候補数・未検証の増減で判断する。
- UIAが乏しい窓(Blender・Unity)は従来のOCR経路のまま。

## 追記1(2026-09-27)

UIAで読めた窓では、達成判定Eの証拠をOCRでなくUIAの名前と入力欄の値から組む(yo-xe承認の速度改善(1))。consult
20260925-2134論点5の反例(名前が同じまま値だけ変わるfill)は、観測時に欄の値を別領域として保持して証拠へ足すことで扱う。
画面外要素は除き、panel・captionはOCR配置の推定なので空にする。OCR経路の窓は従来通り。

- 結果: E2E-03 Finitact 3/3・39.5〜41.9秒(修正前中央値約58秒)。click_emailのrunは約4.0秒→1.4〜1.6秒でverified_successを維持、
  誤達成0(`artifacts/e2e-live/20260927-e2e03-uiaev/`)。
- 未確認: VSCode(Electron、UIA窓)のE live回帰(`scripts/check_achievement_e_live.py --only vscode`)。

## 追記2(2026-09-27)

VSCode(Electron)はUIAに窓枠だけを出し、editor・title barのSearchにeditable要素が無い。「UIA要素あり=UIA経路」のため
09-25の本決定以降fill候補が0件だった。fillが許可された観測で、UIAにeditable要素が1つも無い窓はOCR経路へ戻す。
またOCRで出した候補の達成判定Eは、after観測がUIA経路へ変わってもOCRで証拠を組む(別ソースの比較で全ラベルが
「出現」になり入力値が打ち切られていた)。

- 結果: VSCode Search 4/6 verified_success・document行0/6・誤達成0、Tk 6/6期待通り・誤達成0
  (`docs/evaluations/windows-mcp-replacement/achievement-e-uia-fallback-results.json`)。未達2件は「Harbor lights 42」のJev fit
  不適合で、保存質問の再判定でも0/6(09-25は適合)。Jev側の変化と見て文言調整はしない。
- 未対応: UIA経路の候補でafter観測がOCR経路へ変わる逆向きの混在、canvas本文のclick候補欠落(未観測)。

## 追記3(2026-09-28)

- 追記2の「UIAに編集要素が無ければUIAを捨ててOCRへ」を撤去した(BUG-0040: taskbarのピン留めボタンがUIAにしか無い)。
  UIAは保ち、fill許可かつ編集要素無しの窓だけ全面OCRを読み、UIA要素の下の文字を`:covered`としてfill候補専用に残す
  (VSCodeのtitle bar SearchはUIAボタンの下にある)。pointer・drag・scroll候補とUIA候補のE証拠からは除く。
- UIA窓のOCR候補の再同定はcaret点滅で読みが変わるため、画素同等なら観測時の読みを使う。
- live: VSCode 6/6期待通り・Search 3/3・誤達成0(`bug0040-vscode-results.json`、追記2時は5/6・2/3)。
  taskbar 24→31件(UIA24件+OCR fill)。代償は該当窓のobserveに全面OCR(taskbar 1.7秒・Progman 2.7秒)。

<!-- 現況 -->
2026-10-04: 実装済み(`windows_uia_reader.ComUiaReader`)。候補の単位はADR-0044で窓単位から領域単位へ改めた。
<!-- /現況 -->
