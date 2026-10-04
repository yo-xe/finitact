# ADR-0003: Finitactへ改称し、判断モデル非依存の操作選択層を目指す

- 日付: 2026-09-21
- 状態: 採択
- 決定者: yo-xe + Codex

2026-09-21、プロジェクト名を`jev-ultrafast`から`Finitact`へ変更した。Jevは現行providerで
あって製品境界ではなく、Local Qwen等が優位な局面もあるため、名称と将来設計を特定モデルから
分離する。Finitactは「観測済みの有限候補から操作を選ぶ」性質を表し、まず現行ブラウザ機能を
補完し、次にCoding Agent向けMCP、交換可能なdecision provider、GUI action adapterへ拡張する。

## Considered Options

- `Jev Pilot`: Jev以外を中核に据えにくいため不採用。
- `Selto`: 同じ画面操作AI領域に既存製品があるため不採用。
- `Scopact`: 安全境界は表すが、発音と有限候補選択の表現でFinitactに劣るため不採用。

## Consequences

Python package、CLI、repository、ローカルディレクトリを`finitact`へ統一する。upstream由来の
履歴、実測時のJevモデル名、取得専用`upstream` remoteは保持し、由来とMIT表示をLICENSE/NOTICEへ
明記する。MCP、provider交換、Windows操作は方向性であり、未実装の機能として扱う。
