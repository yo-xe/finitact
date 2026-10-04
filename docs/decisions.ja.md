# 設計判断の一覧

> 2026-10-04時点の内容。English: [decisions.md](decisions.md)

原本の決定記録(`docs/adr/`、日本語)は追記のみで書き換えないため、決定に至る経緯を時系列で含む。本書は現在有効な決定を
主題別にまとめ、各項から原本へリンクする。本書と原本・コードが食い違う時は、原本の最新追記とコードを正とする。

## 現在は有効でないもの

| ADR | 状態 | 理由 |
|---|---|---|
| [ADR-0011](adr/0011-screen-two-action-lease-scope.md) | 置換 | [ADR-0012](adr/0012-screen-2-owned-popup-lease-run.md)へ置換 |
| [ADR-0028](adr/0028-screen-fill-observe-caret-frame.md) | 廃止 | 撮り直しで誤読は消えたが時間中央値が20.0→27.6秒に悪化したため差し戻した |

[ADR-0048](adr/0048-browser-boundary-screen-handoff.md)の決定2(同一originのiframeをCDPで候補化する)は棄却した。

## 1. 方針と範囲

- 観測済みの有限個の候補から操作を1つ選んで実行し、再び観測する処理の繰り返しを基本構成にする。判断providerはJevに限定しない([ADR-0001](adr/0001-project-inception.md)・[ADR-0002](adr/0002-mvp.md)・[ADR-0003](adr/0003-finitact-name-and-direction.md))。
- 先にTypeSafeを使ってMCPの呼び出しから入力の送信までを通しで動かし、その後にproviderを比較する([ADR-0004](adr/0004-mcp-before-provider-comparison.md))。
- provider共通契約は「候補IDか終端理由」だけを返す。確率・複数head・入力の送信は契約に入れない([ADR-0005](adr/0005-provider-neutral-decision-contract.md))。
- アプリ固有の手段(`bpy`・Unity Editor API等)はFinitact自身では使わず、呼び出し側が渡す。Finitactが実装するのはアプリに依存しない画面構造解析(UIA・OCR等)だけ。渡す方法は候補への注釈`app_annotations`と、アプリが出力する状態による達成判定`app_expect`の2つ([ADR-0053](adr/0053-adr-0017-feed.md))。

## 2. 安全な合成入力

- 合成入力は3つの仕組みで守る: プロセス間のnamed mutex、開始前にユーザー操作が止まっていることの確認(idle確認)、実行中の可視indicator。各仕組みが保証する範囲を超えて保証を主張しない([ADR-0008](adr/0008-named-mutex-live.md)・[ADR-0009](adr/0009-synthetic-input.md)・[ADR-0010](adr/0010-indicator-win32-layered-overlay-theme.md))。
- mutexの放棄を検知したらdesktop単位でプロセス内に記憶し、server再起動まで以後の入力を拒否する([ADR-0013](adr/0013-interaction-mutex-desktop-process-sticky.md))。
- idle確認の閾値は設定可能で既定1秒。直前の入力が自runのものだけの時は、idleの起点を次runへ引き継ぐ([ADR-0021](adr/0021-run-idle-run.md)・[ADR-0032](adr/0032-screen-idle-1.md))。
- 送信直前に対象窓を強制前面化し、前面・hit-test検証を通った時だけ送る。入力前に確定した拒否では環境をblockedにしない([ADR-0033](adr/0033-screen.md))。
- 2操作(popupを開いて選ぶ)では観測範囲を同PID・owner chainのowned popupへ広げる。lockはrun単位で保持する([ADR-0012](adr/0012-screen-2-owned-popup-lease-run.md)、[ADR-0011](adr/0011-screen-two-action-lease-scope.md)を置換)。
- 窓間dragは開始窓とdrop窓の2窓契約に限る。drop窓にも保護窓の制限を適用する([ADR-0045](adr/0045-cross-window-drag.md))。
- 入力の到達が不明な時は自動再送せず、環境をblockedにする([ADR-0006](adr/0006-windows-adapter.md)の運用判断、[ADR-0013](adr/0013-interaction-mutex-desktop-process-sticky.md))。

## 3. 観測と候補源

- Windowsのscreen経路は、名前付きのUIA要素を第一の候補源にし、UIAが要素を返さない領域だけOCRで候補を作る。UIAが要素を返さない窓は窓全体をOCRする([ADR-0036](adr/0036-screen-path-com-uia-ocr.md)・[ADR-0044](adr/0044-screen-path-uia.md))。
- 既定OCRはPP-OCRv6 medium+OpenVINO。OCR枠に中心が入るedge候補は落とす([ADR-0007](adr/0007-stage1-extractor.md)・[ADR-0016](adr/0016-stage1-ocr-pp-ocrv6-medium-openvino.md)・[ADR-0018](adr/0018-stage1-ocr-edge.md))。
- scrollはlist塊ごとのup/down候補、fillは一様色panelごとの1候補にまとめる([ADR-0023](adr/0023-screen-path-scroll-list-up-down.md)・[ADR-0029](adr/0029-screen-fill-panel-block.md))。
- scroll領域に隠れているだけの要素は、近い順に最大40個「scroll付き候補」として出し、押す前に可視・最前面を確かめる([ADR-0038](adr/0038-decision.md))。
- 呼び出し元のエージェントが指定するtargetは`window:<HWND>:<PID>`の1種類。経路は`synthetic_input_allowed`で決まる(trueはscreen経路、falseはpattern-only UIA)([ADR-0040](adr/0040-windows-target-1.md))。

## 4. 判断providerとの分担

- 候補へ入力を送れることとその安全性はFinitactが根拠を示して保証する。Jevは実行可能な候補からgoalに合うものを選び、Jevの判断は候補の並べ替えと停止にだけ使う([ADR-0030](adr/0030-finitact-jev.md))。
- 低確信の選択とBLOCKED(確信度0.6未満)は`provider_uncertain`として呼び出し元のエージェントへ返す。呼び出し元のエージェントは候補を直接指定する。goalの言い換えは候補を指定できない時に使う([ADR-0019](adr/0019-bug-0023-label-agent.md)・[ADR-0020](adr/0020-blocked-0-6-provider-uncertain.md)・[ADR-0022](adr/0022-provider-uncertain-agent.md))。
- 3つの手段で、Jevの判断を経ずに実行できるようにする。手段はgoal単位の完全一致label(`click_label_constraints`)、`fill_values`、providerを使わない`find_and_click`である([ADR-0024](adr/0024-screen-click-goal-label.md)・[ADR-0026](adr/0026-screen-fill-goal-fill-values.md)・[ADR-0027](adr/0027-label-click-find-and-click-provider.md))。
- Jevの判断を経ずに実行する指定方法は、goalの`ref`(観測で得た項目ref)1つに統合する([ADR-0041](adr/0041-observe-window-ref-pick.md)・[ADR-0042](adr/0042-run-windows-goal-observe-ref.md)・[ADR-0043](adr/0043-jev-goal-ref.md))。

## 5. 達成判定

- providerのDONEは成功の証拠にしない。達成判定は、Finitactが計算した変化の事実(送信結果・label差分・画素変化域)とJevの適合判断を組み合わせる([ADR-0030](adr/0030-finitact-jev.md)追記)。
- 達成判定はscrollも対象にする。screen clickは画素変化だけで成功とせず、具体的な終状態の証拠を要する([ADR-0031](adr/0031-e-scroll.md)、[ADR-0030](adr/0030-finitact-jev.md)追記4)。
- 起動・送信のような確定操作は、対象に結び付いた終端証拠∧Jevの完了判断で決める([ADR-0035](adr/0035-decision.md))。
- 操作の事後条件は部品の期待状態(checkboxの反転、selectの値、fillの値。WindowsではUIAのToggle・SelectionItemの状態)で照合し、`met / not_met / unknown`を事実として返す([ADR-0039](adr/0039-decision.md))。
- 証拠が無い時は`unverified`のまま返し、誤った成功にしない。

## 6. 呼び出し元のエージェントが使うtoolの引数と結果

- `run_windows`は既定値を持つ引数を省略でき、targetは窓タイトルでも指定できる。評価専用の引数はtool定義から外す([ADR-0034](adr/0034-run-windows-screen.md)・[ADR-0050](adr/0050-windows-target-tool.md))。
- `observe_window`の項目refはgoalの`ref`で直接実行できる([ADR-0041](adr/0041-observe-window-ref-pick.md)・[ADR-0042](adr/0042-run-windows-goal-observe-ref.md))。
- 結果は終了理由・mutation状態・outcome検証を分けて返す([ADR-0004](adr/0004-mcp-before-provider-comparison.md))。`run_id`での再呼び出しは記録済みの結果を返し、入力を繰り返さない([ADR-0006](adr/0006-windows-adapter.md)・[ADR-0051](adr/0051-run-windows-browser-tab-browser.md))。

## 7. browser経路

- browserとWindowsは制御処理を共通の実装にする。制御処理は予算・各判断を1回だけ使うこと・再観測・入力が届いたかの判定・記録である。候補源・観測が最新かの確認・送信・成功証拠は経路ごとに実装する([ADR-0037](adr/0037-browser-windows.md))。
- dragは`extra_operations=["drag"]`のopt-inで、2段で送信する([ADR-0046](adr/0046-browser-drag-opt-in-2-drag.md))。JS dialogは有限候補として扱う([ADR-0047](adr/0047-browser-js-dialog.md))。
- browser経路で操作できない要素(iframe・open shadow root・popup link)の件数を結果に含める。そのタブを表示している窓の`screen_target`も含め、呼び出し元のエージェントがscreen経路へ切り替えられるようにする([ADR-0048](adr/0048-browser-boundary-screen-handoff.md))。
- `run_windows`はbrowser窓のpage goalを、条件が揃い明示的に有効化された時だけbrowser経路で実行する([ADR-0051](adr/0051-run-windows-browser-tab-browser.md))。有効化は`routing=browser_if_singleton`か`FINITACT_WINDOW_BROWSER_ROUTE=1`で行う。

## 8. 評価と公開

- 比較は公開構成のまま行い、両系を同じ判定処理(Finitactの外で対象アプリの終状態を読む)で採点する。公開MCPの結果は書き換えない([ADR-0014](adr/0014-phase-i-outcome-mcp-oracle.md)・[ADR-0015](adr/0015-phase-i.md))。
- 公開比較では全caseでwindow→browser routeを有効にし、その前提をREADMEに明記する([ADR-0052](adr/0052-window-browser-route-case.md))。
- 公開repoには許可リストに載ったファイルだけを、別の新しいgit履歴へ開発repoから一方向にコピーし、評価はレポートとして公開する。外部からのPRはpatchとして開発repoへ取り込む([ADR-0049](adr/0049-repo-export.md))。
