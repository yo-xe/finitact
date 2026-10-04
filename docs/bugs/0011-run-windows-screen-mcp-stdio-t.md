# BUG-0011: run_windows(screen:...)がMCP stdio transport配下でAutomationIndicator.show()のreadiness待ちで20秒hangしSendInputに到達しない

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 8f26ceb

## 症状

- BUG-0010解消後、Phase Fの1click成功caseをWindows-native MCP stdio transport経由で初めてlive試行したところ、
  mutex取得・real idle-time gate通過(idle time自体が常に0秒だった原因はgalleriaの入力デバイスが物理的に
  接続されたまま継続的に入力を発生させていたためと判明し、入力デバイスを外すことで解消・idleは正常に
  増加した)の後、ADR-0009層3のindicator表示待ちで必ずhangする。BUG-0010とは異なる箇所・異なる症状。

## 再現手順

Windows-nativeでMCP serverをstdio起動しrun_windows(screen:...)をclient経由で呼ぶ(scripts/check_windows_mcp_screen_success_live.py)。real idle 30秒以上・mutex取得・capture/extract/provider決定・freshness再capture全て成功後、guard.execute内でAutomationIndicator.show()がreadiness_timeout(5.0秒既定、20.0秒へ拡張しても同じ)ちょうどの秒数でRuntimeError(indicator readiness timed out)となりSendInputへ到達しない。同じ python -m finitact.automation_indicator をPowerShellから直接launchすると0.44秒でREADINESS_TOKENを出力し正常動作する。

## 該当箇所

- `finitact/automation_indicator.py`の`AutomationIndicator.show()`のsubprocess起動。

## 原因

- 子がMCP stdio stdinを継承し、Windowsで同期pipe I/Oが直列化して子の起動がblockした(修正節参照)。
  回帰テスト: `test_show_launches_a_subprocess_with_the_configured_text_and_corner`がstdin=DEVNULLを固定する。

## 修正

- 2026-09-23 修正済み(コミット a04130c): 原因: indicator子プロセスがMCP serverのstdin(MCP stdioパイプ)を継承し、stdio reader threadの保留中同期ReadFileと直列化してWindows上で子の起動がblockした。対処: AutomationIndicatorのPopenにstdin=DEVNULL。A/B(inherit=5.015s timeout / devnull=0.375s ready)とproduction経路3/3で確認。
