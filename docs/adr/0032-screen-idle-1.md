# ADR-0032: screen配送のidle閾値を設定可能にし既定1秒へ下げる

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

ADR-0009層2のidle事前条件は30秒で、評価では開始前にidleを確保していた。実運用ではyo-xeがpromptを打ってEnterを
押してから外側agentが`run_windows`を呼ぶまで数秒しかなく、ほぼ毎回`blocked`になる。30秒に根拠は無く、
2026-09-22の一回限りのidle約91分を正式条件へ格上げした時の値である。

## 決定

- 既定の閾値を1秒にする。キーボード・マウスを操作中の人との衝突だけを避ける。
- 環境変数`FINITACT_MIN_IDLE_SECONDS`(正の秒数)で上書きできる。不正値はrunを拒否する(fail-closed)。
  Windows側serverへは`WSLENV`で渡す。
- ADR-0009の3層構成(mutex・idle・indicator)とADR-0021の持ち越しは変えない。

## 検討した代替案(没案)

- idle門を外しmutexとindicatorだけにする: 操作中の人への衝突を防ぐ唯一の層を失う。
- 閾値を数秒へ下げ固定: 評価(旧条件30秒の再現)や慎重な運用で戻す手段が無い。
- 30秒待ってから配送する: ADR-0021で速度を理由に却下済み。

## 影響

- Phase Iの評価scriptは閾値+2秒待つので、旧条件で再計測するには`FINITACT_MIN_IDLE_SECONDS=30`を指定する。
- 1秒の門は「直前に入力していた」ことしか検知しない。配送中の人の操作との衝突はindicatorの手続き的抑止に頼る(ADR-0009の範囲どおり)。

<!-- 現況 -->
2026-09-25: 採択・実装済み(`finitact/windows_screen_grounded.py` `minimum_idle_seconds`)。live: 合成Shift押下の19秒以内に`claude -p`がuser scope登録のfinitact
(`scripts/finitact-mcp-windows.sh`)経由で使い捨てTkのbuttonをclickし、blockedにならず配送(completed・confirmed)。
<!-- /現況 -->
