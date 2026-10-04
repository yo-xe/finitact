# BUG-0041: 外側agentがrun_windowsでハーネス自身の端末(Windows Terminal、Claudeセッション)をMinimizeし、ユーザーの窓が最大化と縮小を繰り返した。run_windowsの対象に制限が無い

- 報告日: 2026-09-28 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 5ae42f4

## 症状

- (未記入)

## 再現手順

E2E-04パイロット: taskbarの前面化失敗後、agentがrun_windows window:263142:21740 'Minimize this window'を実行(run-390efc6de58e、mutation 4回・verified_success)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-28 修正済み(コミット 6f2655b): run_windowsに対象の拒否が無かった。WindowsRunCoordinatorへtarget_guardを追加し、HWNDの実所有プロセスがFINITACT_PROTECTED_PROCESSES(既定WindowsTerminal/OpenConsole/conhost)ならadapterを開く前にblocked。list_windowsはprotectedを表示。実機でWindowsTerminalのみ拒否を確認
