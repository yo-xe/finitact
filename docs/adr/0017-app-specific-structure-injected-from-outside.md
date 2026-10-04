# ADR-0017: アプリ固有の構造情報はMCP外部から注入し、汎用の画面構造解析だけをMCP内部に取り込む

- 日付: 2026-09-24
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

OCR読取範囲の考察(`docs/evaluations/windows-mcp-replacement/ocr-reading-region-analysis.md`)で、
`bpy`やUnity Editor APIから画面構造を得る道が保留になっていた。画素から読むより速く、正確になりうる。
ただし「アプリ固有手順を足さない」原則とどう両立させるかが未決だった。

## 決定

1. アプリ固有の手段(`bpy`、Unity Editor API、アプリのplugin等)で得た情報は、Finitact MCPが自分で取得しない。
   使う場合は、MCP外部(外側agent等)が取得してMCPへ注入する。
2. アプリを問わず成り立つ画面構造の解析手段(UIA、画素ベースのOCR・edge・GUI element detector、OSの描画差分等)は、
   MCP内部に取り込んでよい。
3. 注入された情報も、観測済み有限候補からの選択と操作直前の再検証を経る。注入の経路と信頼境界は、
   注入を実装する時に決める。

## 検討した代替案(没案)

- **MCP内部にアプリ別adapterを置く**: アプリごとの取得手順がMCPの処理フローに入る。原則に明確に反する(yo-xe)。
- **アプリ固有の手段を使わない**: 速度・正確さの有力な道を捨てる。利用自体は禁止しない(yo-xe)。

## 影響

- `docs/plans/ocr-reading-region-experiment.md`の実験は、汎用手段の範囲で行う。
- 構造情報を注入する入口(MCP requestの形)は未設計。必要になった時に別途決める。

## 追記1: 2026-10-04 注入の入口

決定3の入口はADR-0053で決めた(候補注釈`app_annotations`と状態feed照合`app_expect`、EXP-0015で両方採用)。

<!-- 現況 -->
2026-10-04: 採択。注入の入口はADR-0053で採択・実装済み(追記1)。
<!-- /現況 -->
