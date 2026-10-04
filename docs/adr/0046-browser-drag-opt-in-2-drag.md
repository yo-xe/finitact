# ADR-0046: browser経路のdragをopt-inの2段dragで扱う

- 日付: 2026-09-29
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

`BROWSER_OPERATIONS`にdragが無く、ページ内のカード移動・並べ替え・ドロップ領域への投入をrun_browserでできない。
共有ループは2段drag(開始選択→終点選択、ADR-0045)を既に持つ。

## 決定

- 語彙はopt-in: `run_browser`の`extra_operations=["drag"]`(`RunRequest.extra_operations`、未指定時はfingerprintを従来値に保つ)。
  既定で足すと全ページで候補が膨らむ。旧Agent loopでは受けない。
- 観測: `window.__jevDrag`のときだけ`snapshot.js`が`drag_sources`(`draggable=true`・`aria-grabbed`・親と異なる`cursor:grab/move`)と
  `drop_targets`(drop/zone/lane等の属性一致の最内要素)を返す。`actions`・marker・guardは不変。
- 候補: `BrowserAdapter`が開始候補(`d<n>`、`drag_phase=start`)を足し、`drag_end_observation`で終点候補
  (drop領域→他のdraggable→click候補の順、開始を除き最大60)を作る。終点は`from_node`を持つ。
- 配送: `browser._drag`。mouseMoved→Pressed→10分割Moved→Released。HTML5 draggableは`Input.setInterceptDrags`で
  `dragIntercepted`のdataを受け、終点で`dragEnter/dragOver/drop`を再生する。配送前に両端を可視・hit-testし、外れたらStalePage(入力0)。
- 達成判定Eはdragを対象外(None)のまま。

## 検討した代替案(没案)

- 既定で全ページに開始候補を出す: 候補膨張。
- 終点をclick候補だけにする: 領域(列・リスト)へのdropを表せない。

## 影響

- live未確認(2026-09-29時点)。HTML5 DnDとmouse event系の両方で確認し、`setInterceptDrags`が実Chromeで効くかを見る。
- drop領域の検出は属性・class名の推測で、命名が外れたページでは終点が出ない(他のdraggableとclick候補は出る)。

## 追記1 (2026-09-29): live確認

Playwright Chromium 153(headless、`--remote-debugging-port`)で`Browser`+`BrowserAdapter`を通して実行。HTML5 DnD fixture
(`draggable`カード→`dropzone`)は`dragstart`→`drop`が発火しDOMが移動、mouse event fixture(`cursor:grab`のカード→`lane`)も
`down`→`dropped`。両fixtureとも`drag_sources`/`drop_targets`が検出され、終点候補に領域が出た。`setInterceptDrags`は実Chromeで効く。
未確認: 実サイトのライブラリ製DnD(dnd-kit・react-beautiful-dnd等)。

## 追記2 (2026-09-29): 実ライブラリでのlive確認と配送の調整

`scripts/check_browser_drag_live.py --libs`(fixtureは`scripts/fixtures/browser_drag/`、CDNから読む)で9 fixtureすべて状態が期待通りに変化。
Sortable(native・fallback)、Dragula、jQuery UI draggable/droppable、dnd-kit、@hello-pangea/dnd。実サイトそのものは未確認。

- 検出: 上記ライブラリは`draggable`/`cursor`を付けず、Sortableはpointerdown後にだけ`draggable`を付ける。`snapshot.js`へclass・data属性
  (`aria-roledescription=sortable`、`data-rfd-draggable-id`、`ui-draggable`等)を足した。マークもcursorも無い素のDragulaは、cursorを
  付けたfixtureでのみ検出できる(命名が無い要素は候補に出ない)。
- 配送: Sortableは直前のpointer経路と重なりの向きで挿入位置を決める。HTML5は経路の残りを`dragOver`として再生し(従来は終点のみ)、
  mouse系・HTML5とも終点で約150ms保持してから離す。drop領域ラベルへ`(drop area)`を付け、領域内の先頭カードと区別する。
- 限界: 別リストのカード中心への投入はSortableの挿入判定次第で移動しない(領域への投入は通る)。@hello-pangea/dndは
  drop animationで状態が確定するまで1秒超かかる(投入は成功)。
