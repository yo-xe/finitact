# BUG-0068: Chrome CDPの連続wheelの2回目が移動しない

- 報告日: 2026-10-01 / 状態: 未修正
- 重大度: 中 / 発生コミット: 0b358cb

## 症状

- EXP-0012の専用Chrome profileで、同一tabに560pxのwheelを連続送信すると1回目は移動し、2回目はCDP正常応答でもwheel event・scrollYとも変化しない。3回目は移動する。Webの局所比較6試行すべてで再現。

## 再現手順

使い捨てChrome CDPで長いページを開き BrowserAdapter の同じscroll_downを3回実行。1回目はscrollY=560、2回目はconfirmedでも560のまま、3回目に1120へ進む。`Input.dispatchMouseEvent`を直接送っても同じ。nested領域と文書全体で確認。事前`mouseMoved`追加、2秒待機でも解消しない。

## 該当箇所

- `finitact/browser.py`のwheel配送とChrome CDP側の入力処理。現時点では原因を特定していない。

## 原因

- Chrome内部の原因は不明。2026-10-03の独立CDP診断では、Finitactと同じ背景tab+focus emulationで2回目のDOM wheel eventが欠落し、前面tabおよび同じ背景tabを入力前に前面化した条件では3回とも届いた。座標・delta変更は効かない。CDP成功応答はDOM event到達やスクロール成立を保証しない。[証拠](../evaluations/scroll-loop/browser-wheel-20261003.md)。

## 修正

- 未修正。前面化は同じChromeの対照tabをhiddenへ変えたため、通常profileへ無条件採用しない。DOM `scrollBy`はwheel eventを出さず、透過置換はできない。wheel handler・nested scroller・遅延効果・所有境界の[反例試験](../evaluations/scroll-loop/browser-wheel-20261003.md#反例試験)でも、無移動からの自動再送は正当化されなかった。背景維持かつwheel semanticsを保つ修正案は未確定。
