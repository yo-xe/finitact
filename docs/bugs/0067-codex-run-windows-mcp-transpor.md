# BUG-0067: Codex外側のrun_windowsでMCP transportが閉じ、Windows serverが残存する

- 報告日: 2026-10-01 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 32bb3d2

## 症状

- Codex外側では`list_windows`と`observe_window`は成功するが、taskbarのEdge clickを含む`run_windows`で約1.5秒後に`Transport closed`。外側はBLOCKED、Windows側台帳は`running`、server Pythonが残存する。直接MCP clientでは同じclickが完了した。

## 再現手順

Windows側コード同期後、scripts/run_e2e_live.py finitact --scenario E2E-04 --trials 1 --outer codex --model gpt-6-lunaでtaskbarのMicrosoft Edge clickを実行。Codex streamはrun_windows開始約1.5秒後にTransport closed、Windows台帳はrunningのまま。MCP直結では同じclickが完了する。

## 該当箇所

- `scripts/finitact-mcp-windows.sh`のWindows側MCP stderr経路。

## 原因

- 判定器なしのCodex単発でも`run_windows`後にtransportが閉じ、CLIは終了コード0・stderr空。診断用中継でWindows server子プロセスの終了コード`-13`(SIGPIPE)を記録した。stdoutの読取は継続中だった。stderrを別の開いた先へ向けると同じ呼出しが完了したため、CodexのMCP stderr管への書込みが切断点と判断した。

## 修正

- 2026-10-01 修正済み: wrapperでstderrをstate logへ追記する。通常wrapperでclickとLuna E2E-04完全経路成功を確認した。
