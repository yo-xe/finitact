# BUG-0079: run_browser/observe_browserがChrome消失後の最初の呼出しで間欠的に失敗(listening on…/BU_CDP_URL unreachable/PermissionError pid.tmp)、2回目で成功。tab_id誤りは生のCDPエラー

- 報告日: 2026-10-11 / 状態: 修正済み
- 重大度: 中 / 発生コミット: ec0f497

## 症状

- (未記入)

## 再現手順

Windows MCP server常駐中に専用profileのChromeだけ終了→observe_browser(tab_id=new)。2026-10-11: 3回中3回、1回目失敗(BUG-0078修正後のコード)。同じ終了手順を単独Python 4回/新規プロセス2回で試すと再現せず(3.4秒で復旧)。MCP server固有(複数server共有のbrowser-harness daemon name=default等)が疑いだが未特定

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-10-11 修正済み(コミット 2d650ea): harnessのdaemon Popenがstdin=Noneで、WSL interop経由のMCP serverのstdio handleをdaemonが継承し起動が約35秒止まり親の15秒待ちを超えた。ensure_daemon中だけWindowsの標準入力handleをNULへ差し替える(browser.py _null_stdin)。wrapper経由のstdio MCP clientでChrome強制終了後のobserve_browserが旧コード33〜36秒失敗→修正後3回とも3.3秒(旧コードへ戻す対照で確認)
- 再発防止: none: 実機(WSL interop+Windows handle継承)でしか再現せず自動テスト化できない。計測手順は調査メモに記録

## 調査メモ(2026-10-11、原因は未特定)

- 再現: `scripts/finitact-mcp-windows.sh`を起動したstdio MCP client(WSLのpython `mcp`)から`observe_browser(tab_id="new")`を、専用Chromeを強制終了した後に呼ぶと4/4で失敗(32〜36秒)。エラーは`listening on 127.0.0.1:<port>`(daemon logの古い末尾行)か`fatal: BU_CDP_URL=http://127.0.0.1:<port> unreachable after 30s`。
- 再現しない経路: `cmd.exe /c pyt.bat`で動かす同じ手順(`Browser`直呼び4回、`mcp_server.observe_browser`直呼び5回、2プロセス同時、stdinをpipeにした場合)は全て3.4秒で復旧。差はWSL interop経由の起動(wrapper)に限られる。
- 観察: 失敗時、Chromeはserver(ppid)の子として起動され、daemonに渡したportは起動時のもの。その後`DevToolsActivePort`は別portに変わり、client終了でChromeも消える(server寿命に紐づく)。`browser_harness.daemon`が多数残留しており(8組以上)、2回目のdaemon起動(+15秒)も同じ古いURLを使う。
- 次の手: wrapper経由でdaemonへ渡す`BU_CDP_URL`と`DevToolsActivePort`の食い違い(Chromeが1回目の起動直後に別portで再起動する可能性)を、`launch_dedicated`の返す値と実portを時刻付きで記録して確かめる。残留daemonの掃除も要る。

## 追加切り分け(2026-10-11、Codex、Remote復旧へ一時中断)

- 別profile・別BU_NAMEでもwrapper経由の起動待ち失敗を再現。daemonがCDP接続へ入る時刻が親のlaunchから約35秒後となり、親の15秒待ちを超える。ポートずれは起動遅延と次試行のChrome終了が重なった可能性がある。
- Windows側browser-harness admin.pyのdaemon Popen 2箇所に一時的に `stdin=subprocess.DEVNULL` を足すと、同じstdio MCP clientで3回とも2.9〜3.6秒でbrowser接続まで到達(その先は試験用の未知tab_id拒否)。原因候補はMCP stdinの子への継承。正規tabでの観測・runは未検証。依存パッケージとsourceへの計測用変更は復元済み、製品修正は未実施。
- 証拠はWindows側 `artifacts/bug0079-{trace.jsonl,env.jsonl,imports.log,stacks.log}`。最初の試験にはclientの重複起動があり、単独対照として扱わない。
