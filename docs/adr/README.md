# ADR索引 — finitact

> このファイルは `dev adr index` の生成物。手で編集しない(ADR-0082 D6)。
> 生成日: 2026-10-04 / 53本

| # | 題名 | 状態 | 行 | 追記 | 最終追記 |
|---:|---|---|---:|---:|---|
| 0001 | [プロジェクト発足](0001-project-inception.md) | 採択 | 39 |  |  |
| 0002 | [自用MVPを区切り後に独立プロジェクトへ切り出す](0002-mvp.md) | 採択 | 39 |  |  |
| 0003 | [Finitactへ改称し、判断モデル非依存の操作選択層を目指す](0003-finitact-name-and-direction.md) | 採択 | 22 |  |  |
| 0004 | [TypeSafeでMCP縦断を作ってからproviderを比較する](0004-mcp-before-provider-comparison.md) | 採択 | 14 |  |  |
| 0005 | [decision providerの共通契約から確率と実行責務を外す](0005-provider-neutral-decision-contract.md) | 採択 | 13 |  |  |
| 0006 | [Windowsアクション adapterの暫定契約](0006-windows-adapter.md) | 採択 | 220 |  |  |
| 0007 | [Stage1 extractorの外部依存許容](0007-stage1-extractor.md) | 採択 | 36 |  |  |
| 0008 | [named mutex live検証の実行形態](0008-named-mutex-live.md) | 採択 | 54 |  |  |
| 0009 | [synthetic input排他性の多層防御設計](0009-synthetic-input.md) | 採択 | 89 | 2 |  |
| 0010 | [可視indicatorをWin32 layered overlayと差し替え可能なthemeで実装する](0010-indicator-win32-layered-overlay-theme.md) | 採択 | 75 |  |  |
| 0011 | [screen有界2操作のlease・scope設計](0011-screen-two-action-lease-scope.md) | 置換(ADR-0012) | 89 |  |  |
| 0012 | [screen有界2操作のために観測範囲をowned popupへ広げ、leaseをrun単位で保持する](0012-screen-2-owned-popup-lease-run.md) | 採択 | 92 | 3 |  |
| 0013 | [放棄したinteraction mutexをdesktop単位でprocess内stickyにし、server生存中はhandleを開き続ける](0013-interaction-mutex-desktop-process-sticky.md) | 採択 | 46 |  |  |
| 0014 | [Phase Iのoutcomeは公開MCPを変えず外部oracleで採点する](0014-phase-i-outcome-mcp-oracle.md) | 採択 | 56 | 1 |  |
| 0015 | [Phase I比較の実行条件](0015-phase-i.md) | 採択 | 57 | 1 | 2026-09-23 |
| 0016 | [Stage1の既定OCRをPP-OCRv6 medium+OpenVINOにする](0016-stage1-ocr-pp-ocrv6-medium-openvino.md) | 採択 | 41 |  |  |
| 0017 | [アプリ固有の構造情報はMCP外部から注入し、汎用の画面構造解析だけをMCP内部に取り込む](0017-app-specific-structure-injected-from-outside.md) | 採択 | 38 | 1 |  |
| 0018 | [Stage1はOCR枠に中心が入るedge候補を落とす](0018-stage1-ocr-edge.md) | 採択 | 36 |  |  |
| 0019 | [BUG-0023: 判断器が不確実な時は上位labelを返し外側agentに言い換えさせる](0019-bug-0023-label-agent.md) | 採択 | 42 |  |  |
| 0020 | [BLOCKEDは確信度0.6未満ならprovider_uncertainへ回す](0020-blocked-0-6-provider-uncertain.md) | 採択 | 36 |  |  |
| 0021 | [自runの入力だけが続いた時はidle起点を次runへ持ち越す](0021-run-idle-run.md) | 採択 | 38 |  |  |
| 0022 | [provider_uncertain後は外側agentの候補直接指定を主、言い換えを予備にする](0022-provider-uncertain-agent.md) | 採択 | 39 |  |  |
| 0023 | [screen pathのscrollはlist塊ごとのup/down候補にする](0023-screen-path-scroll-list-up-down.md) | 採択 | 35 |  |  |
| 0024 | [screen click対象をgoal単位の完全一致labelで制約する](0024-screen-click-goal-label.md) | 採択 | 34 |  |  |
| 0025 | [screen dragを二段階選択と一括配送にする](0025-screen-drag.md) | 採択 | 37 |  |  |
| 0026 | [screen fillの入力値をgoal単位のfill_valuesで明示する](0026-screen-fill-goal-fill-values.md) | 採択 | 35 |  |  |
| 0027 | [制約labelの探索とclickをfind_and_clickでprovider無しに選ぶ](0027-label-click-find-and-click-provider.md) | 採択 | 38 |  |  |
| 0028 | [screen fillのobserveでcaret消灯側frameを読む](0028-screen-fill-observe-caret-frame.md) | 廃止 | 40 |  |  |
| 0029 | [screen fill候補を一様色panelごとの1候補に束ねる](0029-screen-fill-panel-block.md) | 採択 | 46 |  |  |
| 0030 | [候補の実行可能性はFinitactが根拠で担保しJevは目的適合と意味づけを担う](0030-finitact-jev.md) | 採択 | 107 | 5 | 2026-10-04 |
| 0031 | [達成判定Eの対象へscrollを加える](0031-e-scroll.md) | 採択 | 32 |  |  |
| 0032 | [screen配送のidle閾値を設定可能にし既定1秒へ下げる](0032-screen-idle-1.md) | 採択 | 34 |  |  |
| 0033 | [screen操作語彙の網羅と配送前の強制前面化](0033-screen.md) | 採択 | 55 | 1 | 2026-09-28 |
| 0034 | [run_windowsの既定値でscreen要求を短くする](0034-run-windows-screen.md) | 採択 | 36 |  |  |
| 0035 | [確定操作の成否を対象に結び付いた終端証拠と完了判断で決める](0035-decision.md) | 採択 | 39 |  |  |
| 0036 | [screen pathの候補源をCOM UIA第一にしOCRを補完と証拠に回す](0036-screen-path-com-uia-ocr.md) | 採択 | 70 | 3 | 2026-09-28 |
| 0037 | [browserとWindowsの判断ループを制御骨格で共通化する](0037-browser-windows.md) | 採択 | 41 |  |  |
| 0038 | [スクロール領域に隠れた要素をスクロール付き候補として出す](0038-decision.md) | 採択 | 44 | 1 | 2026-09-26 |
| 0039 | [操作の事後条件を部品の期待状態で照合し、事実として返す](0039-decision.md) | 採択 | 39 |  |  |
| 0040 | [Windowsの外側向けtargetを1種類にし経路を合成入力許可で決める](0040-windows-target-1.md) | 採択 | 37 |  |  |
| 0041 | [observe_windowの項目refを既存pickの入口で直接実行する](0041-observe-window-ref-pick.md) | 採択 | 44 | 1 | 2026-09-27 |
| 0042 | [run_windowsのgoalにobserve refを置く](0042-run-windows-goal-observe-ref.md) | 採択 | 35 |  |  |
| 0043 | [Jev省略の入口をgoal refへ一本化する](0043-jev-goal-ref.md) | 採択 | 39 |  |  |
| 0044 | [screen pathの候補源を窓単位からUIA被覆の差の領域単位へ](0044-screen-path-uia.md) | 採択 | 64 | 2 | 2026-09-27 |
| 0045 | [窓間dragをdrag限定の2窓契約で扱う](0045-cross-window-drag.md) | 採択 | 41 | 2 | 2026-09-29 |
| 0046 | [browser経路のdragをopt-inの2段dragで扱う](0046-browser-drag-opt-in-2-drag.md) | 採択 | 52 | 2 | 2026-09-29 |
| 0047 | [browserのJS dialogを有限候補として扱う](0047-browser-js-dialog.md) | 採択 | 47 |  |  |
| 0048 | [browserの境界は画面経路へ切り替えられる形で外側へ出す](0048-browser-boundary-screen-handoff.md) | 採択 | 52 | 2 | 2026-10-04 |
| 0049 | [公開repoは許可リストの断面を新規履歴へ一方向exportし評価はレポートとして公開する](0049-repo-export.md) | 採択 | 53 | 3 | 2026-10-04 |
| 0050 | [Windowsのtargetを窓タイトルでも受け外側向けtool定義から評価専用引数を外す](0050-windows-target-tool.md) | 採択 | 39 |  |  |
| 0051 | [browser窓のpage goalを明示opt-inでbrowser経路へ回す](0051-run-windows-browser-tab-browser.md) | 採択 | 59 | 2 | 2026-10-04 |
| 0052 | [公開比較で window→browser route を全caseで有効にし前提条件を明記する](0052-window-browser-route-case.md) | 採択 | 31 |  |  |
| 0053 | [ADR-0017の注入の入口: 候補注釈とアプリ状態feedによる達成判定](0053-adr-0017-feed.md) | 採択 | 53 |  |  |
