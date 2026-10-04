# BUG-0071: E2E-04の最終状態oracleが検索を飛ばした記事復元を成功と判定する

- 報告日: 2026-10-02 / 状態: 未修正
- 重大度: 高 / 発生コミット: 1f76971

## 症状

記事URLとfooterだけで`success=true`になり、taskbar→検索→記事という課題の検索段階が欠けても通る。
統一条件の旧Finitact 5試行を再監査すると1件は検索要求なし、残り4件とwindows-mcp 5件は
検索語を含むtool入力があるだけで、独立した検索送信・遷移証拠は保存されていない。

## 再現手順

2026-09-30統一条件のFinitact試行`e2e04-finitact-1790760320`はEdge起動後の検索入力なしでEnd scrollのみ、最終URL/footerにより`success=true`。保存済みstreamのtool呼出は`list_windows`→taskbar click→`list_windows`→End→observe×2。

## 該当箇所

- `scripts/run_e2e_live.py:e2e04_prepare/e2e04_judge`
- `scripts/build_report_data.py:build_c2`

## 原因

- 開始条件はEdge窓不在のみでprofileの復元状態を確認せず、判定器は最終URL/footerだけを採点した。
- 判定後の`close_edge`はprofile所有を確認せず、最初に列挙したEdge PIDを強制終了していた。

## 修正

- 経路を`pass/fail/unknown`に分け、検索要求のない既知の抜け道を完全経路成功から外した。`pass`にはtaskbar入力、試験所有profileの検索URL→記事URLの履歴、所有Edge窓での終状態を要する。検索語を含むtool要求だけでは`unknown`へ留める。通常profileのピンは入力前に拒否し、cleanupは試験所有processだけを終了する。
- 公開dataの再生成は独立経路`pass`がないE2E-04を拒否する。旧数値はreport本文で撤回した。
- 残作業: 試験専用Windows環境でpin/profileを準備し、復元なし・正規検索・直接URL・別profileの限定liveでgateを確認する。単体の正負例と旧stream再監査だけは完了。その後、両系を同一条件でN=5取り直す。Claude週上限中は本計測不可。
- 2026-10-03 限定live負例: 別profile(未所有dir・所有だが非ピン)は入力前に拒否。復元はprepareのresetで空tab・History 0・route `fail`(no_search_request)。直接URLは3試行とも`fail`(direct_article_url_requested)だが、windows-mcp Typeが同期dialog・stale clipboard・omnibox候補で別入力になり、記事到達(終状態成功)+経路failの組は未再現(単体試験のみ)。trial前のclipboard消去をharnessへ追加。
