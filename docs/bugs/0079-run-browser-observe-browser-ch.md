# BUG-0079: run_browser/observe_browserがChrome消失後の最初の呼出しで間欠的に失敗(listening on…/BU_CDP_URL unreachable/PermissionError pid.tmp)、2回目で成功。tab_id誤りは生のCDPエラー

- 報告日: 2026-10-11 / 状態: 未修正
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

- (dev bug close で記入される)
