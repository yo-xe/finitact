# ADR-0025: screen dragを二段階選択と一括配送にする

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Codex(Astra反証相談)

## 背景

dragは始点と終点の二判断を要するが、始点で`act()`すると未完の選択をmutationとして記録し、button保持中に
provider判断を待つことになる。一方、全始点×全終点を一度に候補化すると候補数が積で増える。配送途中の失敗時は
button releaseを試みつつ、成功扱い・自動再送を避ける必要もある。

## 決定

- 始点はtext由来候補から選ぶが入力・`act()`・action budget消費を行わない。二度目のprovider判断は既存のattempt予算へ数える。
- adapterは選択済み始点と、同一windowのOCR・edge各終点を組にしたcomposite候補を返す。候補数は終点数に比例させる。
- 終点決定後のcompositeだけを1 mutationとして既存`act()`へ渡す。retained pickとdecision cacheにも段階・両端を明示する。
- 配送直前の一つのcaptureで両端を再同定し、native呼出し内でも両端のPID・foreground・hit-testを検査する。
- native配送はdown、決定的な8中間move、終点move、upを一括で行う。down後の不足・例外ではupを試みるが、
  結果はuncertainのまま環境をblockし、自動再送しない。

## 検討した代替案(没案)

- 始点選択を非mutationの`act()`として返す: 現行journal・action budget・retained observation契約と衝突する。
- 専用coordinatorへprovider loopを複製する: attempt予算・cache・uncertain処理が二重化する。
- 始点×終点を一判断で提示する: 二判断要件を満たさず候補数が積で増える。
- downしたまま終点判断を待つ: 判断中のcancel・deadline・provider失敗で入力状態を安全に閉じられない。

## 影響

screen pathの明示opt-in operationへ`drag`を追加する。始点選択だけではmutation historyに残らず、最終配送だけが
1 actionになる。edge終点はshape由来であることと位置を明示してuncertain後のpick対象にも残す。Tk・Unity・Blenderでの
成立はoffline契約から分離し、live検証する。

<!-- 現況 -->
2026-09-25: 採択。offline 357件合格。使い捨てTk DnD・Blender Outlinerでlive各3/3合格。
<!-- /現況 -->
