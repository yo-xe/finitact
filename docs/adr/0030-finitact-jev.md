# ADR-0030: 候補の実行可能性はFinitactが根拠で担保しJevは目的適合と意味づけを担う

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

BUG-0033・BUG-0034の誤fillは、OCRの誤読でもJevの判断ミスでもなかった。OCRは文字を正しく読み、Jevは渡された
label(tooltipの「Search — …」、caption「Name:」)からgoalに合う候補を選んだ。原因はFinitact側の2点にある。

- 候補生成: edge輪郭以外の全OCR領域へ根拠なく`operation: fill`を付け、「入力できる」という推定を事実の形で渡した。
- 配送: click後にfocusが対象へ移ったかを確かめずCtrl+A+貼付を送り、前提が外れると既存入力を全置換した。

候補生成層がいま根拠を持って付けられる情報は、OCR(文字列・rect・確信度・抽出元)、画素(一様panel所属、枠輪郭、
行stack、末尾`:`caption対、包含)、Win32(hwnd/popup scope、hit-test、foreground)、操作後にだけ得られるもの
(wheel後の変化、caret大の画素差分、MSAA caret)に限られる。操作前の観測に「入力欄である」という事実は無く、
枠・panel・captionは状況証拠にとどまる。直接の証拠はclick後のcaretだけである。

## 決定

- Finitactは候補の実行可能性(その操作が意味を持ち安全に配送できること)を、根拠のある観測か配送時の確認で担保する。
  根拠のない推定を事実の形でJevへ渡さない。
- Jevは実行可能な候補からgoalに合うものを選ぶ。加えて、状況証拠の意味づけ(画面状態・変化の意味・達成判定など)を
  型付き判断として返してよいが、安全の保証には使わず、並べ替え・絞り込み・停止判断の入力にとどめる。
- fillは「click→Finitactが入力受付を確認→文字送出」の3段とし、確認はFinitactの責任とする。MSAA caret門(BUG-0033、
  d6e6fe6)はそのWindows実装の1つで、caret取得不可時は素通しになる。OS非依存の確認(画素差分等)と、fill候補を
  入力欄らしい領域へ絞る候補側の対処が本命で、未着手。
- 達成判定は変化の事実(配送結果・label差分と対象との位置関係・画素変化域)をFinitactが計算してJevへ渡す構成を検証中
  (EXP-0005)。変化が起きた部品(本文panelか新規overlayか)を事実へ足すのが次の検証。

## 検討した代替案(没案)

- 「入力欄か」をJevに判定させてfill可否を決める: 実行可能性の保証をJevへ移すことになる。EXP-0004で領域役割は82%、
  一時表示の識別は75%(BUG-0033のtooltipを常設と判断)で、安全の根拠に足りない。
- MSAA caret門を本解とする: Windows限定で、caretを公開しないアプリ(自前描画のBlender・Unity想定、未実測)では素通しになる。
- 達成判定を直接問う(EXP-0004項目6、EXP-0005方式A): 値の文字列が現れれば達成とする近道を取り、単純規則と同じ誤りをする。

## 影響

- 実測(`docs/experiments/0004-jev-9.md`、`0005-jev-goal.md`): Jevの画面単位の型付き判断はp50 0.2秒・入力2〜5k token。
  EXP-0004で基準(90%・規則+10pt)を満たしたのは変化の意味づけ(10/10)のみ。EXP-0005では事実経由の達成判定がTk 100%
  (A 90%)、VSCode 75%(A 88%)、計88.2%(A 87.6%)。本文へのfillを全方式が「Search入力を達成」と誤る(BUG-0033型を検出できない)。
- 未解決: caret取得不可率の実測、OS非依存の入力受付確認、fill候補の絞り込み、変化部品を足した達成判定。

## 追記1(2026-09-25)

Q-0001(達成判定を実装へ入れるか、D・Eのどちらか)をE(`landed_conn`版)で決定する。yo-xe指示は「Eの規則部が局所解で
ないか監査し、問題なければE実装を採用」。監査で`landed`は局所解と判明し(標本外で溢れ・折返しを未到達、tooltip型を到達と
誤る)、連結画素変化域で読む`landed_conn`へ置き換えた。置換後に採取した未見標本で到達18/18・誤達成0件(EXP-0007採択)。

- 達成=Finitactの到達規則(`landed_conn`)∧Jevの「この操作が効けばgoalを満たすか」。達成と言えない時は未検証として
  再観測・停止判断へ回す。安全の保証には使わない(決定2のまま)。
- Dは採らない: 標本外で46〜56%と規則並みに落ち、Search未入力をSearch達成とする誤達成が残る。
- 既知の限界: Jevの適合判断が保守側に外れ、held-outの達成再現は44〜56%。1行文書への行fillを「文書全体を置換」に
  不適合とする型が全件(17件)。Jevへの文言調整でなく、適合の問いへ渡す事実(対象部品・文書の範囲)で直す。

## 追記2(2026-09-25)

1行文書fillの「文書全体を置換」不適合は既知の限界として受け入れる(yo-xe判断)。適合の問いへ事実を足す方針は、保存標本の
再判定(`docs/evaluations/windows-mcp-replacement/achievement-e-fill-scope-replay.json`)で不成立だった。fill手順・対象panelの
文字(操作前/後)では文書fill適合0/33のまま。fillの効果範囲を明示すると33/33だが、Search fillの適合が5/30→0/30へ落ちるため、
文言依存と判断した。外れても未検証として再観測へ回るだけで誤達成はない。損はverify約0.7秒で、VSCode置換+保存caseは
E導入前の計測(Finitact 20.0秒 / windows-mcp 27.2秒)に約0.7秒を足した程度に留まる。

## 追記3(2026-09-28)

BUG-0039: 消えるpopup(自動入力候補・dropdown項目など、選択と同時に閉じてDOM/screenから消える要素)内のclick/fill選択を、
screen・browser両経路で達成判定Eの対象にした(d6dcc79)。従来は対象外(None、外側agentがDONE判断を自前で行っていた)。

- 実装: popup内の選択も`landed_conn`∧Jev適合の対象に含め、popupが閉じた後の親画面の変化から到達を読む。
- live回帰: `check_achievement_e_live.py`12/12期待通り・誤達成0(2026-09-28)。
- 影響計測(BUG-0039修正後、同commit基準で再計測):
  - Unity select(`screen-unity-select-resolution-001`、live 3試行): 3/3成功、誤達成0。時間29.6秒(ADR-0044基準30.3秒と同等)、
    外側token中央値21.2k(基準29.3k、-28%)、ツール呼び出し2回(基準3〜4回)。
  - E2E-03(live 3試行): 3/3成功。21.2〜23.3秒・外側63k〜65k(基準19.3〜26.1秒・65kと同等)。
  - E2E-02(live 6試行): 6/6成功、誤達成0。中央値178.2秒(基準170秒と同等)、外側token中央値0.90M(基準0.79M、+14%)。
    windows-mcp基準(263.8秒・5.31M)に対する優位は維持。token増はpopup経路の追加判定コストとみられ、時間・成功率への
    影響は誤差内のため様子見(悪化が続けば追加調査)。
  - 記録: `docs/evaluations/windows-mcp-replacement/unity-select-bug0039-live-results.jsonl`、
    `docs/evaluations/e2e-live/results.jsonl`(E2E-03・E2E-02のBUG-0039後分)。

## 追記4(2026-10-01)

BUG-0069: `landed_conn`が画素変化だけで成立したclickは、対象にフォーカスや選択色が付いた事実までしか示さない。
Jevのfit回答も要求対象との誤対応を実機で起こした。対象位置の文字状態の置換、owned popupの出現、閉じたpopupの
値反映など具体的な終状態の証拠がないscreen clickは`unverified`に留める。別窓出現・文字の移動による終端判定は
既存の独立経路を使う。語句抽出による自由文の厳密一致は同義語・popupを損なうため採らない。

元goal・fixture・候補集合の実TypeSafe再試験は誤行click 3/3、誤成功0/3。制御providerで正しい行をクリックすると
独立oracleは選択済みだが、現行screen証拠では`unverified`。Tk ListboxはUIAに選択状態を公開しない。
この制限を受容し、選択済みの独立証拠を得る方法は別課題とする。
`docs/evaluations/scroll-loop/bug0069-effect-gate.md`参照。

## 追記5(2026-10-04)

決定節の「OS非依存の入力受付確認と、fill候補を入力欄らしい領域へ絞る候補側の対処」は実装せず、既知の限界として受容する
(yo-xe判断)。候補側はUIAの編集可能要素(ADR-0036)、一様panelの1候補化(ADR-0029)、caption対で一部を絞った。
caretが読めないアプリ(自前描画のBlender・Unity想定)のOCR fill候補では、click後の受付確認なしでCtrl+A→送出に進む
(`windows_screen_grounded._caret_gate`、caret不明時は素通し)。公開比較C1/C2のfill対象(Notepad・VSCode・Discord・Chrome)は
UIAかcaretで確認できるため影響しない。利用者向けには`docs/known-issues.md`に記す。

<!-- 現況 -->
2026-10-04: 採択。実行可能性はFinitactが根拠で担保し、Jevは目的適合と意味づけを担う原則で運用中。達成判定Eは
screen clickの画素変化のみで成功とせず具体的な終状態の証拠を要求する(追記4)。caret不明時のOCR fillの受付確認は
実装せず限界として受容(追記5)。Tk Listboxの正しい行選択は`unverified`となる。
<!-- /現況 -->
