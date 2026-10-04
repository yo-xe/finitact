# ADR-0013: 放棄したinteraction mutexをdesktop単位でprocess内stickyにし、server生存中はhandleを開き続ける

- 日付: 2026-09-23
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

Phase FのLeaseAbandoned liveでADR-0009層1(named mutex)の穴を2件確認した。
BUG-0014: named mutexは最後のhandleと共に消えるため、ownerが唯一のhandle保持者のままcrashすると
`WAIT_ABANDONED`が誰にも観測されず、次のrunは新しいmutexを取得して配送へ進む。
BUG-0015: `WAIT_ABANDONED`で得た所有権はserverのpool threadが解放せずに保持する。Win32 mutexは
再帰取得できるため、同じthreadで走る別`exclusive_environment_ref`のrunは待たずに取得でき、
ref単位の`InputEnvironmentState`のblockを素通りしうる。

## 決定

1. 放棄はmutex名(= session/window station/desktop)単位でprocess内stickyにする。
   `WAIT_ABANDONED`と`ReleaseMutex`失敗を記録し、以後の`WindowsNamedInteractionLease.acquire`は
   Waitせず`LeaseAbandoned`で拒否する。refが違っても同じdesktopなら拒否する。
2. 放棄で得た所有権は意図的に解放しない。他のFinitact processはserver終了までtimeoutで
   fail-closedになる。復旧はserver再起動(人間が環境を確認してから)。
3. processはmutex handleを名前ごとに1本、生存期間中開き続ける(`ProcessMutexState`)。
   MCP serverはWindowsで起動時に現在desktopのhandleを開く(`keep_current_desktop_mutex_open`)。

限界: Finitact processが1つも生きていない間のowner crashは検知できない(mutexごと消える)。
起動時のhandle取得に失敗した場合は最初のsynthetic runまで検知範囲が縮む(stderrへ記録)。

## 検討した代替案(没案)

- **放棄で得た所有権を即解放する**: 他processが状態不明の環境へ配送を再開できてしまう。
  放棄は「直前の配送結果が不明」を意味するため、fail-openになる。
- **ref単位のblockだけに頼る(現状)**: 同じdesktopを別refで指すrunが再帰取得で素通りする(BUG-0015)。
- **handleをrun中だけ開く**: run間のcrashを取りこぼす。常駐serverなら生存期間中の保持で費用はhandle 1本。

## 影響

- `finitact/windows_interaction_lease.py`: `ProcessMutexState`導入。acquireは保持handleでWaitし、
  都度のCreate/Closeをやめる。
- `finitact/mcp_server.py`: Windows起動時にhandleを開く。
- `scripts/check_windows_mcp_lease_abandoned_live.py`: observer handleを廃止し、server起動後にholderを
  放棄させる。別refのrun 3が`interaction lease was abandoned`、別process試行がtimeoutであることを判定に含める。

<!-- 現況 -->
2026-09-23: 採択・実装済み。offline test 246件、galleriaでlease-abandoned/mutex/two-action probe成立。
<!-- /現況 -->
