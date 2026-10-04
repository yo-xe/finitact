# BUG-0040: taskbar(Shell_TrayWnd)のピン留めアイコンがUIA候補に出ずOCRの断片(Q/o/K)だけになる。さらにtaskbar宛てのclickは前面化できず'foreground stayed on'でblockedになる

- 報告日: 2026-09-28 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 5ae42f4

## 症状

- (未記入)

## 再現手順

E2E-04パイロット(artifacts/e2e-live/e2e04-pilot-1): observe_window window:<Shell_TrayWnd>が7件(Q,検索,o,K,あ,日付,時刻)のみ。run_windowsでEdgeアイコンのclickを約25回試行し、provider_uncertainまたはdelivery target verification failed: foreground stayed on Windows PowerShell

## 該当箇所

- `finitact/screen_grounded_adapter.py` observe() の ADR-0036追記2分岐(`elements and "fill" in self.operations and not any(e.editable ...)` で `elements = None`)

## 原因

- UIA readerはtaskbarを直接読めば24件(ピン留めボタン全て、0.07秒)を返す(2026-09-28実機)。しかしfillが許可された観測
  (observe_windowは常に click+fill、run_windows既定もfillを含む)でUIAに編集可能要素が無いと、追記2がUIA全体を捨てOCRのみにする。
  taskbarは編集要素が無いのでOCR 7件だけになった。デスクトップ(Progman、UIA 30件)も同じ分岐でUIAを失っている。
- 被覆率では区別できない(実測 taskbar 0.52、VSCode 0.32・127要素、Progman 0.10)。VSCodeのtitle bar SearchはUIAボタン
  「Open Quick Access」になっており、editor本文はUIA被覆外のOCRに残る。

## 修正方針(候補源は実装済み、ADR-0036追記3)

- 分岐を撤去してUIA経路を保ち、UIAに編集要素が無い時だけfill候補をUIA被覆外のOCR領域(`not _is_uia(r)`)から `_fill_candidates` で作る。
- 再同定の一貫性: `_fill_still_there` もOCR領域だけで組み直す。act の resolve_target `elif uia:` 分岐でfill panel/caption候補を
  `_fill_still_there` へ回す(現状はregion id一致のみで、panelのsubject `panel:<id>` は一致しない)。
- 前面化('foreground stayed on')の原因: 端末が最後の実入力を持つ間、常駐MCP serverのlock timeout解除だけでは
  taskbarを前面化できない(常駐probe 3/3失敗、通常windowも間欠失敗)。`AttachThreadInput`併用で修正(ADR-0033追記1、
  常駐probe 6/6)。MCP経路はE2E-04 N=3で確認してからclose。
- 回帰(実施済み、6/6): VSCode Search(追記2の `achievement-e-uia-fallback-results.json` と同条件)とtaskbar前面化。

## 修正

- 2026-09-28 修正済み(コミット ddd772e): 候補源: ADR-0036追記2の分岐がUIAを捨てていた→UIAを保ちfillだけOCRから作る(ADR-0036追記3、f4761b5)。前面化: 端末が最後の実入力を持つ間、常駐MCP serverのlock timeout解除だけではtaskbarを前面化できない→AttachThreadInput併用(ADR-0033追記1)。E2E-04 N=3で3/3がtaskbar→Edge→記事→footerまで到達
