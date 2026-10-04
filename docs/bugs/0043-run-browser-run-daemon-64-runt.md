# BUG-0043: run_browserの初回runがdaemon起動待ち約64秒の後 RuntimeError('listening on 127.0.0.1:52705 (name=default, remote=local)') で失敗する。daemonはlistenまで進むのにping(daemon_alive)が60秒間成功しない

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 中 / 発生コミット: dffed53

## 症状

- (未記入)

## 再現手順

E2E-06b Finitact試行1(artifacts/e2e-live/e2e06b-n3-finitact-20260929、run-c64a956d0db6)。直前にWindows側CDP Chromeを起動し直した直後の最初のrun。同じ外側agentの再試行は8秒で成功。冷えたdaemon・古いdaemon残存+Chrome再起動の2条件では再現せず(1.3秒以下)。restart_daemonはbu-default.portのunlinkでPermissionError(WinError 5)になることがある

## 該当箇所

- `finitact/browser.py` の `start_harness`(browser_harness の `ensure_daemon` を既定60秒待ちで呼んでいた)

## 原因

- 機序は未確定。再発2件目: E2E-11 Finitact試行1(`artifacts/e2e-live/e2e11-n3-finitact-20260929/`、run-e58896eeecce)。
  新規profileのCDP Chrome起動直後の初回runが60秒後に `RuntimeError: conn: Connection lost`(daemonログ末尾)で失敗、
  次のrunは6秒で成功。
- probe `scripts/probe_daemon_chrome_restart.py`: 生存daemon下のChrome入れ替え(待ち2秒・30秒、既存/新規profile、
  daemon停止あり)計15回はいずれも0.6〜4.5秒で60秒待ちは再現せず。1回だけ `restart_daemon` が
  `bu-default.port` のunlinkで `PermissionError [WinError 5]` を即時に投げた。
- 暫定対処(未close): CDP接続時は `ensure_daemon(wait=15)` で待ちを区切り、chrome-not-running/permission系以外の失敗なら
  daemonを本人確認つきで停止しendpointを消して1回だけ起動し直す(`_reset_daemon`)。Windows実機でreset 0.03秒・再起動0.45秒。
  再発時は約15秒+再起動で済むはず。journal上の所要で確認してからcloseする。
- 再発3件目(暫定対処後): E2E-12試行1(`artifacts/e2e-live/e2e12-n3-finitact-reach-20260929/`、run-9c0383608efc)。
  CDP Chrome起動直後の初回runが4.9秒で `PermissionError [WinError 5]`(`bu-default.port`)。harnessの`ensure_daemon`は
  stale daemonで`restart_daemon`を呼びport fileをunlinkするため、`_reset_daemon`(unlink再試行3秒)後の2回目の
  `ensure_daemon`から同じ例外が漏れたと推定(tracebackは残っていない)。次のrunは成功。
- 根本修正の調査(2026-09-29、未完): harnessの`restart_daemon`は`ipc.cleanup_endpoint`と`os.unlink(pid_path)`で
  `FileNotFoundError`しか捕まえないので、port fileのunlinkが`PermissionError`なら`ensure_daemon`の外へ漏れる。
  WinError 5はWindowsでdelete-pending(誰かのhandleが開いたまま既に削除済み)のファイルへの再unlink・readでも出る。
  またWindowsの`os.kill(pid, 0)`は生存確認でなくTerminateProcessになるので、`restart_daemon`の終了待ちloopは
  shutdown中のdaemonを即時に落とす。どちらが効いているかは未確定。probeはtraceback全文を出すよう変更済み。
  次: CDP Chromeを起動した状態で`scripts/probe_daemon_chrome_restart.py --warm --kill-wait-s 5 --trials 8`を回し、
  PermissionErrorのtracebackを得てから直す(Chrome未起動で回した1回目はwarmで`BU_CDP_URL unreachable`となり無効)。
- 根本対処(2026-09-29): CDP起動直後のprobe 8回(kill待ち5秒)は0.95〜1.02秒で全成功し、tracebackは得られなかった。
  harness側の2つの欠陥(`restart_daemon`がPermissionErrorを漏らす・Windowsで`os.kill(pid, 0)`が停止中daemonを落とす)
  を経路から外した。`start_harness`はCDP接続時に古いdaemon(pingは通るが`Target.getTargets`が失敗)を
  自前の`_reset_daemon`で先に止め、harnessが`restart_daemon`に入らないようにした。`_reset_daemon`はharnessを使わず、
  shutdown要求→`_pid_running`(Windowsは`WaitForSingleObject`)で終了待ち3秒→残れば強制終了→endpoint削除を
  最大10秒再試行し、消えなければ理由付きの`RuntimeError`にする。Windows実機のprobe(warm、kill待ち5秒・0.5秒 各6回)で
  12/12・1.03〜1.11秒、毎回daemonが入れ替わった。E2Eの初回runで再発しないことを確かめてからcloseする。

## 修正

- 2026-09-29 修正済み(コミット d120658): harnessのrestart_daemon(PermissionErrorを漏らす・Windowsでos.kill(pid,0)が停止中daemonを落とす)を経路から外し、古いCDP daemonをFinitactの_reset_daemonで先に止める(shutdown→WaitForSingleObjectで終了待ち→endpoint削除を再試行)。機序の再現は取れず。修正後、古いdaemon残存+新規profile Chrome起動直後のE2E-06b 3/3・7.8〜17.4秒(artifacts/e2e-live/e2e06b-n3-finitact-bug43fix-20260929/)、実機probe 12/12。
