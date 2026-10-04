# ADR-0004: TypeSafeでMCP縦断を作ってからproviderを比較する

- 日付: 2026-09-21
- 状態: 採択
- 決定者: yo-xe + Codex

Finitactは、最小のprovider非依存契約を抽出した後、現行TypeSafe providerでCoding Agent向け
MCP縦断を先に完成させ、その後にLocal Qwenを同条件で比較する。Qwenの採否はMCP化の価値と独立であり、
先に比較を必須化すると不採択時にもnorth starのMCP到達を遅らせるためである。

共通契約はobserved candidatesからの選択と終端判断だけを要求し、TypeSafe固有の複数head・確率分布、
DOM表現、text helperを含めない。MCP結果は実行終了理由、mutation状態、outcome検証状態を分離する。
Windows action adapterはbrowser契約から演繹して固定せず、限定UI Automation試作後に共通境界を確定する。
具体的な段階、検証指標、停止条件は`docs/plans/finitact-mcp.md`で管理する。
