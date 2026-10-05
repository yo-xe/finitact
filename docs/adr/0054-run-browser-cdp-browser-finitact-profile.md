# ADR-0054: run_browserは利用者のCDP browserが無ければFinitact専用profileのChromeを起動する

- 日付: 2026-10-05
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

BUG-0078。`BU_CDP_URL`未設定時、browser経路は利用者の既存Chromeへ接続する設計だった。
接続にはchrome://inspectのリモートデバッグ許可と接続ごとのAllow popupが要る。harnessの自動起動は既定profileを開くが、
Chrome 136+は既定profileで`--remote-debugging-port`を拒むため、利用者の操作なしにCDPは開かない。
BUG-0042の修正はこの起動を先行確認で塞いだので、Chrome未起動では即`chrome-not-running`で止まり、外側agentは別手段へ回った。
MCP serverはWSL経由でも素のWindowsでもWindows-native Pythonで動く。このため問題と修正は両者で同じである。

## 決定

`BU_CDP_URL`/`BU_CDP_WS`が無い時の`start_harness`は次の順で接続先を選ぶ(`finitact/browser_launch.py`)。

1. 生きていてCDPに応答するharness daemonはそのまま使う。応答しないdaemonは止める(BUG-0043と同じ扱い)。
2. 利用者のbrowserがCDPを待ち受けていれば従来どおり接続する。判定は既知profileの`DevToolsActivePort`の
   portか9222/9223へのTCP接続だけで行う。HTTP/WSを送ると利用者のChromeにAllow popupが出るためである。
3. それ以外は、Finitact専用profileを使う。既定は`%LOCALAPPDATA%\finitact\browser-profile`、
   非Windowsは`$XDG_STATE_HOME/finitact/browser-profile`、`FINITACT_BROWSER_PROFILE`で変更できる。
   起動中ならそれに接続する。無ければ`--user-data-dir=<専用> --remote-debugging-port=0`で起動し、
   `DevToolsActivePort`のportへ`BU_CDP_URL`としてdaemonを繋ぐ。Chromeを優先し、無い時だけEdgeを使う。
   実行fileは`FINITACT_BROWSER_PATH`かApp Paths・既定install先から探す。
4. 専用profileで走ったrunは`final_state.browser_profile="finitact"`を返す。外側agentが「利用者のsign-inが無い」と判る。

利用者のChromeが起動していてもCDPを待ち受けていなければ、専用profileのChromeを別instanceとして起動する。
従来のchrome://inspect案内(`remote-debugging-setup`)へは、この経路では入らない。

## 検討した代替案(没案)

- 起動可否を事前判別して返すだけ: 外側は人に頼むか別手段へ回るしかなく、BUG-0078が残る。
- WSL側のheadless browser経路: process構成が二重になる。素のWindowsでは意味を持たない。
- 利用者の既定profileを`--remote-debugging-port`付きで起動: Chrome 136+が拒む。sign-in済みprofileを無承認で開く問題もある。
- 専用profile起動をopt-in: 既定の失敗が残る。利用者のCDP browserは常に優先するので、既定にしても既存の利用法を壊さない。
- 生存確認を`supported_browser_running`(tasklist)で行う: 専用Chromeの`chrome.exe`も数えてしまい、利用者browserの有無を区別できない。

## 影響

- Chrome未起動でも`run_browser`が始まる。Windows実機では初回起動2.3秒、再利用0.2秒、強制終了後の再起動3.5秒だった。
- 専用profileはsign-inを持たない。sign-inが要るsiteでは、利用者がその窓で一度sign-inすれば以後もprofileに残る。
- 専用Chromeの窓は利用者のdesktopに出て、run終了後も残る(tabはrunごとに閉じる)。
- CDP portは127.0.0.1だけで待ち受けるが、同じ利用者のlocal processからは無承認で操作できる。`BU_CDP_URL`運用と同じ前提である。
- Edgeしか無い環境では、新規profileがWindows accountで自動sign-inし履歴をsyncする(K-78d7db696377)。
- `run_windows`のbrowser経路(ADR-0051)は`BU_CDP_URL`明示時だけのまま。専用profileへは広げていない。

<!-- 現況 -->
2026-10-05: 採択。実装済み(Windows実機で起動・再利用・再起動を確認)。
<!-- /現況 -->
