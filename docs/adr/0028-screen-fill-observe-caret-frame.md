# ADR-0028: screen fillのobserveでcaret消灯側frameを読む

- 日付: 2026-09-25
- 状態: 廃止
- 決定者: yo-xe + Claude Code(反証相談`docs/consult/20260925-0925-20260925-caret-fill-target-topic.md`)

## 背景

BUG-0032: caret点灯中のframeでは同じ行が`DLDVALUE`(conf 0.846)と読まれ、消灯中は`OLDVALUE`(1.0)と読まれる。
Win32からcaretの位置は取れない。BUG-0031では化けたlabelのせいで本文候補の得点が落ち、fill先の誤選択へつながった。

## 決定

- `fill`が有効なobserveでは、1枚目のOCRと並行して0.55秒後に2枚目を撮る(`caret_recapture_seconds`。Windows factoryだけが有効化する)。
- 2枚の差分がcaret形状の箱1つで、差分箱の上下端の帯が1枚目では外側背景と大きく異なり(>=48)、2枚目では背景に一致する(<=24)
  時だけ2枚目を読み直す。判別できない時(差分なし・caret形状でない・箱がframe端に接する)は1枚目を読む。
- fill先の選択はproviderに残す。caret差分を入力先の証拠としてproviderを省く案(B)は採らない。

## 検討した代替案(没案)

- OCR confidenceが高い方の読みを採る: 誤読側のconfidenceが高い場合に誤りを固定する。今回は消灯側が高かったが、それは一般には保証されない。
- 単一frameで細い縦線を消す: `I`・`l`・`|`・罫線と区別できない。
- caret一意+`fill_values`でproviderを省いてfillする(B): 本物のcaretがgoalとは別の欄にあり得て、ADR-0026の
  「入力欄の選択はproviderに残す」を越える。fill後も条件が成立し続け、再fillし続ける。
- 全observeで2回撮る: fillが無効なcaseでは得る物が無い。

## 影響

- 追加遅延は1枚目がcaret点灯側だった時の再OCRだけ(行cropの認識cacheに当たるので主に検出の分)。2枚目の撮影は1枚目のOCRと重なる。
- 2枚の間隔では点滅の位相を保証できない。「差分なし」は判定不能という意味で、BUG-0032を全面的には解消しない。
- BUG-0031(初手uncertain)が解けるかは、labelの修正とは別にliveで判定する。

<!-- 現況 -->
2026-09-25: 廃止(実装5d66a74、差し戻しは次commit)。live 5本(Windows側`artifacts/phase-i/caret-recapture-vscode-fill-x5`)で
5/5成功し、`DLDVALUE`は0件になった。ただし時間中央値は20.0→27.6秒、外側tokenは16.9k→17.7kに悪化した。
fill後のobserveは1.6→3.0秒: extractがcacheに当たるとrecaptureの待ちが表に出る。初手uncertainは12件残り、
前回x5bもlabelは全部`OLDVALUE`だったので、BUG-0031の初手uncertainはcaretの誤読が原因ではない。
軽い代替案(yo-xe、2026-09-25): 英字では縦棒による誤読は`O→D`・`o→b/d`・`n→h`・`c→d`・`I/l/|`の挿入などに限られる。
撮り直さずに置換表で吸収できる可能性がある(CJK・数字は対象外)。
<!-- /現況 -->
