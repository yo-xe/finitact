# BUG-0029: Windows MCP server内のfill text helper(deepseek)がValueError(no valid field value)で止まる。WSLから同じcontextで4回呼ぶと全て成功

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 752215a

## 症状

- (未記入)

## 再現手順

Windows側でphase_i_casesのVSCode caseを準備し、WSLのmcp clientからrun_windows→screen_candidatesのOLDVALUE fill候補をpick(artifacts/phase-i/vscode-fill-x5/repro/mcp_pick_client.py、server-stderr2.logにtraceback)。1/1でmodel.field_text:461のValueError。in-process実行では成功。応答本文の記録用パッチ後の再試行ではfill候補が出ず未確認

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット b6b3710): 実providerのdeepseek-chatが既定温度で、値を明示したgoalにも{"text": null}を返す(5/58)。field_textが無効値としてValueError。非OpenAIのtext helperをtemperature 0にし(0/57)、goal明示値の逐語返却と画面labelが現在文字列でありうる旨をTEXT_VALUEへ追記。Windows実経路30/30で期待値
