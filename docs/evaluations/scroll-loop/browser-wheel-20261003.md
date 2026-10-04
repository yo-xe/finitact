# BUG-0068: 背景Chrome tabのwheel配送診断 (2026-10-03)

## 結論

使い捨てChrome profileへの直接CDPで、Finitactと同じ背景tab条件が2回目のwheel event欠落を再現した。前面tabは同じHTML・同じ入力で3回とも届いた。座標とdeltaの微変更では改善しない。Chrome内部の原因は未特定であり、`Input.dispatchMouseEvent`の成功応答をwheel到着の証拠にできない。

同じ背景tabを`Page.bringToFront`した後はheaded/headlessともwheelが3回連続で届いた。しかし同じChromeの対照tabは`visibilityState=visible`から`hidden`へ変わった。通常profileでの無条件な前面化はユーザータブの状態を変えるため製品へ入れない。DOM `scrollBy`も3回ともscrollYを進めたがwheel eventを出さないため、透過的な置換には使えない。

## 条件と結果

`scripts/diagnose_browser_wheel_live.py`(座標条件は`--tab-mode coordinate_matrix`、前面化条件は`--tab-mode background_promoted`、各`--headed`有無)。Windows-native Python、Chromeの試験専用一時`user-data-dir`、独立CDP port、同じ60行HTMLを使用。`Target.createTarget(background=true)`で背景tabを作成し、Finitactと同じ`Emulation.setDeviceMetricsOverride(1120x780)`および`setFocusEmulationEnabled(true)`を設定。条件ごとにpageを読み直し、3回の560px wheel入力を各0.2秒以上離して送った。各入力の前後で`window.scrollY`と捕捉したDOM `wheel` event数を読んだ。

| 条件 | 1回目 | 2回目 | 3回目 |
|---|---|---|---|
| 前面tab、通常wheel | 560 / event+1 | 1120 / +1 | 1599 / +1 |
| 背景tab、通常wheel | 560 / event+1 | 560 / +0 | 1120 / +1 |
| 背景tab、2回目のx座標変更 | 560 / +1 | 560 / +0 | 1120 / +1 |
| 背景tab、2回目のdeltaを561 | 560 / +1 | 560 / +0 | 1121 / +1 |
| 背景tab、DOM `scrollBy` | 560 / +0 | 1120 / +0 | 1624 / +0 |
| 同じ背景tabを入力前に前面化 | 560 / +1 | 1120 / +1 | 1624 / +1 |

headless/headedと背景tabの4座標で同じ2回目欠落。focus emulationの再設定、off→on切替も欠落を直さなかった。focus emulationなしの背景tabではwheelのCDP応答が10秒でtimeoutし、focus emulationありでは通常応答だが2回目のDOM eventがない。metrics overrideだけではtimeoutした。`Input.synthesizeScrollGesture`も背景tabでCDP応答が10秒以内に戻らず、中断試行は完全な記録になっていない。

生記録: [headed座標](wheel-probe-headed-matrix-20261003.jsonl)、[headless座標](wheel-probe-headless-matrix-20261003.jsonl)、[CDP flags](wheel-probe-flags-20261003.jsonl)、[座標・delta変更とDOM scroll](wheel-probe-alternates-20261003.jsonl)、[focus切替](wheel-probe-focus-reset-20261003.jsonl)、[前面化headed](wheel-probe-promoted-headed-20261003.jsonl)、[前面化headless](wheel-probe-promoted-headless-20261003.jsonl)。これらは各条件1回の局所試行であり、異なるChrome環境の再現率ではない。

## 製品判断の境界

現在の共通runループの誤行clickやWebの入力前confidence停止は別の失敗。今回のwheel欠落は、固定Web試験でscrollが実行される後段の欠陥として扱う。`confirmed`はCDP応答であり、DOM event到達・scroll移動・goal達成と区別する。Astraの反証相談(非公開)は前面化を条件付き第一候補としたが、通常profileでの副作用が実測された。次の製品案を採る前に、wheel handler、nested scroller、遅延配送、通常profileの所有境界を反例として固定する。

## 反例試験

`scripts/check_browser_wheel_counterexamples_live.py`で、同じ試験専用profile内に対照tabと所有背景tabを作成した。所有tabは本番と同じmetrics・focus emulationを設定し、背景維持と入力前の前面化を別profileで比較した。headed/headless各1回、各fixtureの入力前後を保存した。生記録は[headed](wheel-counterexamples-headed-20261003.jsonl)と[headless](wheel-counterexamples-headless-20261003.jsonl)。

| 反例 | 背景維持 | 入力前に前面化 | 判断 |
|---|---|---|---|
| `preventDefault()`するwheel handler、3入力 | event 3回・`scrollY=0` | event 3回・`scrollY=0` | 無移動から欠落を推定できない |
| nested scroller、3入力 | 内側`scrollTop` 0→560→560→1120、event 2回 | 0→560→1120→1680、event 3回 | 内側の移動とeventを別に読む |
| nested末端でwheel 1入力 | 内側2280のまま、外側`scrollY` 0→560 | 同左 | 外側だけの移動もscroll chainingで起こる |
| wheel handlerが1秒後に`scrollBy`、1入力 | CDP応答後0.2秒は`scrollY=0`、1.4秒後560 | 同左 | 早い無移動観測は安全な再送根拠にならない |
| 対照tabの状態 | `visible`維持、wheel 0 | `visible`→`hidden`、wheel 0 | 所有tab限定でも他tabの表示状態を変える |

試験はDOMで効果を遅延させたもので、CDPの配送そのものが遅延した証拠ではない。既存の[mutation失敗テスト](../../../tests/test_agent.py)は、wheel呼出が例外を返した場合に`MutationUncertain`で再送を止める。今回の反例からは、背景tabのままwheel semanticsを保ち、通常タブ状態も保つ修正は見つからなかった。BUG-0068は未修正とし、前面化は試験所有profileだけの候補に留める。
