# BUG-0072: E2E-04の所有Edge profileがWindowsアカウントで自動sign-inしsync由来の過去visit(検索→記事)をHistoryへ入れ、route gateが閲覧由来と区別せず偽passしうる

- 報告日: 2026-10-03 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 52ba163

## 症状

- route gateが所有profileのHistoryを区別なく読む。sync由来の過去visitで偽pass、終了前の未flushと未decode URLで正規経路を`unknown`にした(live 2件)。

## 再現手順

ピンを所有profileへ向けE2E-04を1試行。History 844visit中 visit_source=0(sync)699・4(import)137・行なし8。sync分にgoogle検索→ja.wikipedia有限オートマトンがある

## 該当箇所

- `scripts/run_e2e_live.py`の`e2e04_history`・`e2e04_route`・`e2e04_judge`

## 原因

- 新規user-data-dirのEdgeがWindowsアカウントで自動sign-inしてhistoryをsync・importする(visit_sourceあり)。
- Edgeは最後のvisitを終了時までHistoryへ書かない。HistoryのURLはpercent-encodeで、記事prefix比較がdecodeしていなかった。

## 修正

- 2026-10-03 修正済み(コミット 52ba163): visit_source行のあるvisit(sync/import)を除外、所有Edgeを閉じた後にHistoryを読む、記事URLをunquoteして比較。修正後のlive(windows-mcp N=1)で正規経路pass。影響2行はresults.jsonlのissuesに注記
