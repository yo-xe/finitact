# ADR-0023: screen pathのscrollはlist塊ごとのup/down候補にする

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

screen pathはOCR regionの有限候補から選ぶ。scroll(`docs/plans/screen-input-ops.md`手順2)を足すには、
どこを・どちらへ回すかを候補として出し、loopが端で止まれる必要がある。

## 決定

- operation `scroll`は`allowed_operations`でopt-inする。
- 候補はx区間が重なり縦の隙間が行高の1.5倍以内で連なるtext region 3行以上の塊ごとに`up`/`down`の2件。
  窓幅の6割超のregionとedge contourは連結に使わない。labelは塊の先頭・末尾の語。
- wheelは塊の中心へ1 notchずつSendInputで送り、notch数は塊の高さの約2/3を行ピッチ×3行で割って1〜10。
  配送前検査はkeyと同じ全体の再capture同等性とclickと同じhit-test。
- wheel後の観測が送る前と同等(0.3秒後の再captureでも同等)なら、その塊・方向を以後の候補から外す。
  同じ塊が動いたら外した方向を戻す。

## 検討した代替案(没案)

- 全region×方向: 候補が3倍に膨れ、判断の遅延と誤選択を招く。
- 窓全体の上下2候補だけ: 複数のscroll領域(sidebarと本文)を区別できず、wheel位置が領域外に落ちうる。
- 端の判定をproviderに任せる: 画面が変わらない事実は機械で判る。providerに読ませると反復が止まらない。

## 影響

- Tk list(find・end)とEdgeのja.wikipedia長記事でlive合格(記録は計画手順2)。
- ブラウザではタブ列が本文塊へ連結されlabelが不正確になる。先頭でもupは1回試すまで出る。

<!-- 現況 -->
2026-09-25: 採択。実装済み、live合格。
<!-- /現況 -->
