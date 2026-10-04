# ADR-0051: browser窓のpage goalを明示opt-inでbrowser経路へ回す

- 日付: 2026-10-04
- 状態: 採択
- 決定者: yo-xeのE2E-02改善指示 + Codex(Astra相談)

## 背景

2026-10-03のE2E-02はFinitact 2/5。失敗3件はChrome上のSQS数値欄を`run_windows`で埋められなかった
(BUG-0073)。外側tokenの25〜100%を最初の`run_browser`までに消費した
(`docs/evaluations/e2e02-turns-20261004.md`)。現行tool説明は`run_windows`がUIA/OCRとWindows入力を使うと約束する。
相談2件(`docs/consult/20261004-0008-e2e02-browser-routing-question-20261004.md`、
`docs/consult/20261004-0012-e2e02-singleton-routing-followup-20261004.md`)はPID+titleだけの自動振分を退けた。

## 決定

1. `run_windows`に`routing=browser_if_singleton`を追加する。既定は`windows_only`。運用者は
   `FINITACT_WINDOW_BROWSER_ROUTE=1`で、引数を省いた要求のみopt-inにできる。browser chrome、画面ref引継ぎ、
   UIA限定要求は`windows_only`を使う。tool説明にpage内操作への限定を明記する。
2. Windows-native、ローカル`BU_CDP_URL`、CDP browser PIDとHWNDの実PID一致、当該PIDの表示窓1件、
   CDP page target1件、窓title一致、`Browser.getWindowForTarget`成功、pageがvisibleでURL/titleが一致する時だけ候補とする。
   route検査はraw CDPの副作用禁止付き評価を使い、`Browser.observe()`を呼ばない。frame・open shadow root・
   popup linkの検出または検査不能なら画面経路。選んだtargetIdを固定し、originはその時点のURLのoriginだけ。
3. 初版は1 goal・既定操作集合・合成入力の既定・画面ref等なしの要求に限定する。browserの操作集合は
   `click/fill/select/scroll/wait`。Windowsの`key`/`set_range`等と意味を同一とは扱わない。browser開始後に
   BLOCKED等で元要求を画面経路へ自動再送しない。結果にrouteと適用origin・操作集合を返す。
4. browser/Windowsの既存実行台帳は維持し、その外側の共通永続受付で元要求のfingerprintと実行経路を
   `run_id`ごとに原子的に固定する。再送では再判定せず同じbackendの記録を読む。実行中/中断不明は再実行を拒む。

## 検討した代替案(没案)

- PID+titleだけで複数窓・複数tabを対応付ける: HWNDと選択tabを証明できない。
- 既定で全browser窓を自動委譲: browser chromeへのfill等の意味を変え得る。既存tool契約も変わる。
- `Browser.observe()`で到達外要素を検査する: 保留入力を再開し得る。
- backend台帳を物理統合する: 同形式だが別契約の履歴と計測資料を移す必要があり、二重受付の解決には共通入口だけで足りる。

## 影響

使い捨てChromeの単一tabでは、外側が`run_windows`を選んでもpage操作をCDPへ渡せる。既定の
Windows操作は維持する。singletonとDOM検査は汎用の完全な同一性証明ではないため、複数窓・複数tab、
閉じたshadow root等は未対応。E2E-02の改善量はlive再計測で確定する。

## 追記1(2026-10-04): ページ全体のframe・shadow root・popup link除外を撤廃

E2E-02スモーク(N=1)で外側は`browser_if_singleton`を指定したが、計算機ページはiframe 1・`_blank` link 167を常に持ち一度もroutedにならなかった。
相談(`docs/consult/20261004-0145-e2e02-route-exclusion-question-20261004.md`)でrun_browser直呼びより悪化する反例は無く、popup linkは
`snapshot.js`が候補から除くため除外根拠が無い。決定2のframe・open shadow root・popup link条件を外す。frame内対象は旧画面経路より
BLOCKEDが増え得るが自動再送しないため費用増に留まる。検査側の件数はsnapshotと数え方が違い矛盾を渡すため結果に載せず、
`final_state.out_of_reach`を正とする。

## 追記2(2026-10-04): target窓が所有するtool window popupをsingleton判定から外す

E2E-02計測cの棄却`owned windows 2`の2件目は、前試行のCSV保存後に残るChromeのダウンロードバブル(本体窓が所有する
`WS_EX_TOOLWINDOW`のpopup)だった。バブルはページ外でCDP入力を妨げない。`list_windows`は所有されたtool windowへ`popup_of`を付け、
route判定はtarget窓の`popup_of`だけを除外する。ファイル選択等のモーダル(非tool window)と他窓所有のpopupは従来どおり棄却する。

<!-- 現況 -->
2026-10-04: 採択。opt-in実装済み。使い捨てChromeのlive検査でsingleton page=routed、iframe page=screen(`scripts/check_browser_window_route_live.py`)。追記1でページ内frame等の除外を撤廃し計算機tabでrouted確認。計測c(`docs/evaluations/e2e02-route-20261004.md`)でF 4/5。追記2でダウンロードバブル残存時もrouted(実Chrome確認)。
<!-- /現況 -->
