# 既知の問題

> 2026-10-04時点の内容。English: [known-issues.md](known-issues.md)

不具合の原本(`docs/bugs/`、日本語)は追記のみで、発見時の再現記録をそのまま残す。本書は未修正の問題を利用者への影響で
説明し、修正済みの記録を領域別に索引する。新しい問題はGitHub issueで報告してほしい。

## 未修正(9件)

### Finitactの利用に影響するもの

- **[BUG-0023](bugs/0023-screen-210-blocked-0-2.md) 候補が多い画面でJevが正解の代わりにBLOCKEDを選ぶ(中)**
  Windowsのscreen経路で候補が約210件あると、BLOCKEDが低い確率(0.2前後)のまま相対最多となり、操作せずに止まることがある。
  確信度0.6未満のBLOCKEDは`provider_uncertain`として候補一覧とともに呼び出し元のエージェントへ返る。呼び出し元のエージェントはgoalの`ref`で候補を
  直接指定して続けられる([ADR-0020](adr/0020-blocked-0-6-provider-uncertain.md)・[ADR-0022](adr/0022-provider-uncertain-agent.md))。Unity解像度メニューの「16:9 Aspect」はアプリ知識の不足として今も残る。
- **[BUG-0073](bugs/0073-run-windows-chrome-aws-sqs-uia.md) browser窓のラベル無し数値欄へscreen経路で入力できない(中)**
  ChromeのAWS料金計算機のように、UIA名が無くplaceholderしか見えない数値欄は、`run_windows`の既定(screen経路)では
  fill候補の確信度が0.14〜0.39で止まる。条件が揃うbrowser窓では`FINITACT_WINDOW_BROWSER_ROUTE=1`
  (または`routing=browser_if_singleton`)でbrowser経路へ回すと入力できる([ADR-0051](adr/0051-run-windows-browser-tab-browser.md))。
- **[BUG-0068](bugs/0068-browser-wheel-2.md) 背景のChrome tabで連続したwheelの2回目が動かない(中)**
  CDPで同じ背景tabへwheelを続けて送ると、2回目だけwheel eventが届かずスクロールしない。CDPは成功を返す。3回目は動く。
  tabを前面化すると直るが、同じChromeの他のtabを隠すため製品の修正にしていない。スクロール後に次の観測で位置を確かめること。
- **[BUG-0074](bugs/0074-bug-0043-run-browser-run-runti.md) `run_browser`の初回が内部daemonの起動待ちで失敗することがある(低)**
  初回のrunが`RuntimeError('listening on 127.0.0.1:…')`で失敗する。同じ要求を再度呼ぶと通る。[BUG-0043](bugs/0043-run-browser-run-daemon-64-runt.md)の再発。
- **[BUG-0076](bugs/0076-typesafe-httpx-httperror-run-e.md) JevのAPIへの接続失敗で再試行せずrunが止まる(中)**
  HTTP 429/503/529は再試行するが、接続例外は即座に送出され`Model connection failed; no action executed.`で終わる。
  対象へは何も送っていないので、新しい`run_id`で呼び直してよい。
- **[BUG-0077](bugs/0077-windows-interaction-lease-wait.md) 入力中のrunを強制終了すると以後の`run_windows`が全て失敗する(中)**
  Windowsのrunはシステム全体の入力mutexを共有する。保持中のprocessが殺されると、次のrunがabandonedのmutexを受け取り、
  解放せずに`interaction lease owner exited unexpectedly`を送出するため、新しいprocessでも以後のrunが同じく失敗する。
  再起動では解消せず、mutexを一度待って解放する単発scriptで解消する。

### 評価と比較対象に関するもの(Finitactの動作には影響しない)

- **[BUG-0071](bugs/0071-e2e-04-oracle.md) E2E-04(taskbar→検索→記事)の判定器が検索を飛ばした試行を成功とする(高)**
  経路の証拠を`pass/fail/unknown`に分け、独立した`pass`の無い旧成績は撤回した。公開しているE2E-04の数値は2026-10-04の取り直しで、経路が`pass`の試行だけを成功とする。
- **[BUG-0062](bugs/0062-e2e-04-edge-uia-found-false-wi.md) E2E-04の判定器がEdgeの記事末尾をUIAで見落とし、成功を失敗とする(中)**
  再走査を足したが、効いた試行がまだ無く効果は未確認。
- **[BUG-0063](bugs/0063-c2-e2e-05-windows-mcp-batch-09.md) 比較対象(windows-mcp)の`Type`が操作者の端末へ入力された(中)**
  windows-mcpは打鍵時に前面窓を確かめず、並列実行された他の操作で前面が移ると別の窓へ入る。Finitactは送信直前に対象窓の
  前面化・hit-test・idleを確かめるため同じ経路を持たない([ADR-0032](adr/0032-screen-idle-1.md)・[ADR-0033](adr/0033-screen.md))。レポートの安全性の観察に載せている。

## 受容した制約

- **caretが読めないアプリのOCR fillは、入力前に受付を確かめない。** 自前描画でcaretを出さないアプリ(Blender・Unity)では、
  OCR fill候補をclickした後、入力欄が受け付けたかを確かめずにCtrl+Aと文字列を送る([ADR-0030](adr/0030-finitact-jev.md)追記5)。
  UIAかcaretで確かめられる入力欄(Notepad・VS Code・Discord・Chrome)は影響しない。次の観測で値を確かめる。
- **Tk Listboxの正しい行選択は`unverified`として返る。** 選択状態を読み戻せないため。
- **`app_expect`のfeedが正しいかはFinitactが確かめない。** feedは呼び出し元のエージェントとアプリ側pluginの責任で、操作後に書かれた一致値だけを
  `outcome_verified`の根拠にする。誤ったfeedは誤判定になりうる([ADR-0053](adr/0053-adr-0017-feed.md))。

## 修正済み・対応不要(67件)の索引

### browser経路

- [BUG-0001](bugs/0001-browser-mutation-history.md) mutationの途中失敗が履歴に残らない
- [BUG-0002](bugs/0002-bug.md) MutationUncertainの期待値がtestの場合分けを狭めた
- [BUG-0004](bugs/0004-nested-scroll-control-popup-li.md) nested scroll外のcontrolとpopup linkが通常のclick候補になる
- [BUG-0005](bugs/0005-wikipedia-action-table.md) 透明な言語セレクタが操作表から漏れる
- [BUG-0042](bugs/0042-run-browser-cdp-browser-chrome.md) CDP対象のbrowserが無い時、失敗まで約120秒かかる
- [BUG-0043](bugs/0043-run-browser-run-daemon-64-runt.md) 初回runがdaemon起動待ちで失敗する([BUG-0074](bugs/0074-bug-0043-run-browser-run-runti.md)で再発)
- [BUG-0044](bugs/0044-run-browser-goal-search-all-se.md) 報告だけを求めるgoalで依頼外のclickをする
- [BUG-0045](bugs/0045-run-browser-js-dialog-confirm-.md) JS dialogが開くと以降の操作がtimeoutする
- [BUG-0064](bugs/0064-c2-e2e-02-finitact-agent-claud.md) 入力欄の標準制約を拒否として報告する

### Windowsの合成入力と安全

- [BUG-0012](bugs/0012-automationindicator-show-ws-ex.md) 実行中indicatorが前面を奪う
- [BUG-0014](bugs/0014-windows-mutex-owner-crash-is-i.md) 他にhandleが無いとmutex所有者のcrashを検知できない
- [BUG-0015](bugs/0015-after-wait-abandoned-the-serve.md) 放棄mutexの所有権がpool threadに残る
- [BUG-0017](bugs/0017-125-screen-grounded-pointer-ca.md) 表示倍率125%でpointerが別座標をclickする
- [BUG-0025](bugs/0025-screen-run-finitact-synthetic-.md) 自runの合成clickがidle計測を戻し、次のrunが止まる
- [BUG-0033](bugs/0033-screen-fill-pointer-hover-tool.md) fillがhover tooltipを選び本文を置き換える
- [BUG-0034](bugs/0034-tk-entry-name-oldvalue-88x21-f.md) 小さなEntryへのfillがラベルへ送信される
- [BUG-0036](bugs/0036-screen-fill-caret-bug-0033-dis.md) caret確認がフォーカス済みの空欄を誤って拒否する
- [BUG-0041](bugs/0041-agent-run-windows-windows-term.md) 呼び出し元のエージェントがハーネス自身の端末を操作できた(対象制限の追加)
- [BUG-0055](bugs/0055-fill-set-clipboard-null-block.md) 空文字fillでclipboard設定が失敗し環境をblockする

### Windowsのtaskbar・Start・検索

- [BUG-0040](bugs/0040-taskbar-shell-traywnd-uia-ocr-.md) taskbarのピン留めアイコンが候補に出ず、clickも前面化できない
- [BUG-0047](bugs/0047-start-list-windows-taskbar-she.md) Start/検索を開くとtaskbarが窓一覧から消える
- [BUG-0048](bugs/0048-start-click-searchhost-foregro.md) Start click後の検索窓・Start窓がどちらもtargetとして拒否される
- [BUG-0051](bugs/0051-taskbar-click-post-mutation-ob.md) Start click後の再観測が失敗する
- [BUG-0052](bugs/0052-settings-applicationframewindo.md) 設定窓が前面だとtaskbarのStart clickが届かない
- [BUG-0053](bugs/0053-taskbar-fill-searchhost-key-bl.md) taskbar検索欄へのfillで前面が移りkeyが届かない
- [BUG-0054](bugs/0054-searchhost-taskbar-click-shell.md) 検索窓が前面だとtaskbar clickが拒否される
- [BUG-0057](bugs/0057-dwm-cloaked-2-searchhost-corew.md) 非表示の検索窓が前面を握るとtaskbar操作が届かない
- [BUG-0058](bugs/0058-taskbar-key-goal-press-enter-e.md) taskbarへのEnterが達成を認識できず繰り返される

### 観測と候補

- [BUG-0013](bugs/0013-warm-up-native-extractor-runti.md) extractorの初回loadでhangする
- [BUG-0018](bugs/0018-stage1-tesseract-ocr-popup-1-t.md) tesseractが小さなpopupの文字を読まない
- [BUG-0020](bugs/0020-pp-ocr-extractor-1-valueerror.md) 文字の無い画像でOCR extractorが例外を出す
- [BUG-0026](bugs/0026-provider-uncertain-screen-labe.md) 差し戻し時のlabelに機械的な領域名が入る
- [BUG-0031](bugs/0031-screen-fill-ocr-dldvalue-jev-3.md) fill候補がOCR誤読ラベルで低得点になり別の場所へfillを選ぶ
- [BUG-0032](bugs/0032-text-caret-ocr-oldvalue-dldval.md) 撮影時のtext caretでその行のOCRが化ける(対応不要: 撮り直しは速度を損ない、[ADR-0028](adr/0028-screen-fill-observe-caret-frame.md)で廃止)
- [BUG-0037](bugs/0037-uia-provider-uncertain-screen-.md) UIA経路の差し戻しで返したrefが使えない
- [BUG-0046](bugs/0046-run-windows-win11-uia-document.md) Windows 11メモ帳の本文がfill候補に出ない
- [BUG-0049](bugs/0049-observe-window-screen-ref-synt.md) observe_windowのrefが`synthetic_input_allowed=false`のrunで使えない
- [BUG-0050](bugs/0050-uia-set-range-tool-runs-py-fil.md) UIAの範囲設定(slider)へ到達できない
- [BUG-0056](bugs/0056-screen-windows-uia-slider-obse.md) screen経路でUIA sliderが候補に出ない
- [BUG-0061](bugs/0061-c1-vs-code-fill-save-finitact-.md) 入力済みの欄へ同じ値を再fillし予算を使い切る
- [BUG-0065](bugs/0065-run-windows-list-windows.md) 同名の最小化窓があると窓タイトル指定が曖昧エラーになる
- [BUG-0066](bugs/0066-unity-dropdown-goal-target-sta.md) dropdownが開いた後も送信を繰り返す

### 判断providerとmodel呼び出し

- [BUG-0003](bugs/0003-model-call-budget-decision-pro.md) model呼び出し予算が成功した判断だけを数える
- [BUG-0019](bugs/0019-ollamadecisionprovider-prompt-.md) local providerでpromptが文脈長を超え黙って切り詰められる
- [BUG-0021](bugs/0021-screen-typesafe-255-http-400-m.md) 候補が255件を超えるとHTTP 400で止まる
- [BUG-0022](bugs/0022-screen-210-typesafe-http-400-m.md) 候補210件以上でtoken上限超過になる
- [BUG-0029](bugs/0029-windows-mcp-server-fill-text-h.md) text helperが既定温度で値を返さないことがある

### 達成判定

- [BUG-0039](bugs/0039-popup-role-option-e.md) 消えるpopup内の選択が達成判定の対象外になる
- [BUG-0069](bugs/0069-scroll-item-15-outcome-verifie.md) scroll探索中に別の行を選んでも検証済みと報告する

### 実行基盤・MCP transport

- [BUG-0006](bugs/0006-powershellbridge-cp932-stderr-.md) PowerShell bridgeがCP932のエラー出力を化けさせる
- [BUG-0007](bugs/0007-windows-native-python-cp932-fi.md) cp932既定のWindows Pythonでimportに失敗する
- [BUG-0008](bugs/0008-test-stage1-extractors-py-linu.md) testがLinux専用のfont pathを使う
- [BUG-0009](bugs/0009-tkinter-2-tk-thread-windows-fa.md) Tkを別threadで作り直すとWindowsでfatal exception
- [BUG-0010](bugs/0010-run-windows-screen-mcp-stdio-t.md) stdio MCP配下で候補抽出中にhangする
- [BUG-0011](bugs/0011-run-windows-screen-mcp-stdio-t.md) stdio MCP配下でindicatorの準備待ちがhangする
- [BUG-0028](bugs/0028-run-windows-stdio-mcp-ledger-r.md) 実行中にMCP接続が切れるとrunが永久にrunningで残る
- [BUG-0035](bugs/0035-e-live-tk-second-second-entry-.md) 達成判定のlive確認中にPythonがnative crashする
- [BUG-0067](bugs/0067-codex-run-windows-mcp-transpor.md) Codexを呼び出し元のエージェントにするとtransportが閉じserverが残る

### 評価ハーネス

- [BUG-0016](bugs/0016-unity-dropdown-probe-escape-un.md) Unity dropdown probeの後始末でpopupが閉じない
- [BUG-0024](bugs/0024-blender-oracle-state-writer-os.md) Blenderの状態書き出しがファイル置換の権限エラーで止まる
- [BUG-0027](bugs/0027-phase-i-unity-open-case-resolu.md) Unity caseが開始時の解像度を固定しない
- [BUG-0030](bugs/0030-phase-i-tk-scroll-oracle-state.md) Tk scrollの判定器が書き込み途中の空ファイルを読む
- [BUG-0038](bugs/0038-e2e-03-prepare-brave-30-timeou.md) Brave未起動時に準備が30秒timeoutする
- [BUG-0059](bugs/0059-phase-i-runner-screen-case-cou.md) 無人時にscreen caseの準備が前面化できず全件失敗する
- [BUG-0060](bugs/0060-case-oracle-ocr-windows-mcp-ru.md) 電卓caseの判定器がキー入力後の表示を読めない
- [BUG-0070](bugs/0070-claude-is-error-true-subtype-s.md) Claudeの利用上限で終わった試行が有効試行に入る
- [BUG-0072](bugs/0072-e2e-04-edge-profile-windows-si.md) 試験用Edge profileのsync由来履歴で経路判定が偽passしうる
