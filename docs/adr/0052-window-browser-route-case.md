# ADR-0052: 公開比較で window→browser route を全caseで有効にし前提条件を明記する

- 日付: 2026-10-04
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

ADR-0051のrouteはopt-in(`routing`明示か`FINITACT_WINDOW_BROWSER_ROUTE=1`)で、E2E-02ではF 2/5→4/5に効いた
(`docs/evaluations/e2e02-route-20261004.md`)。公開比較の測定条件に含めるか、caseごとに切り替えるかが未定だった。
routeは窓・tab・CDP endpointの状態が揃った時だけ成立するため、利用者がその条件を知らないと効かない。

## 決定

1. 公開比較のFinitactは全caseで`FINITACT_WINDOW_BROWSER_ROUTE=1`とする。運用者のshellに任せず、ハーネスが固定する
   (`outer_agent.FINITACT_BENCHMARK_ENV`。Claude Phase I設定・Codex設定・E2Eの親process環境)。
2. route成立条件をFinitactでブラウザ操作する際の前提条件としてREADMEに明記する(英語版正、日本語版から参照)。

## 検討した代替案(没案)

- caseごとにON/OFF: Windows専用caseでは`no local CDP endpoint`で即座に画面経路になり差が出ないうえ、測定条件が不揃いになる。
- 既定(製品既定)をONにする: 普段使いのChromeでは条件を満たさず効かない一方、利用者の意図しない経路切替になる。製品既定はopt-inのまま。

## 影響

- 平等test条件(`docs/plans/public-repo.md`)にFinitact設定の行が増える。旧C1/C2値はroute無しの断面として新断面と混ぜない。
- 前提条件を満たさない環境では画面経路のままで、READMEの条件が説明の正になる。

<!-- 現況 -->
2026-10-04: 採択。ハーネス固定とREADME明記を実装済み。新断面の本計測は未実施。
<!-- /現況 -->
