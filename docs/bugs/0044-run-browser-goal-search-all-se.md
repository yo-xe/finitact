# BUG-0044: run_browserの報告だけを求めるgoal(「Search all servicesを選び検索して結果を報告」)で、providerが検索結果のカード(QLDB)まで押して設定ページへ進んだ。依頼外のmutation

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 低 / 発生コミット: dffed53

## 症状

- goal末尾に「and report results」があると、検索fill後の達成判定E(Jev effect fit)がFalseになり、loopが続いて
  providerが検索結果カード(Configure QLDB)をclickしてからDONEを返す。outcomeはunverified。
- 2026-09-30再現(WSL headless Chromium、`BU_CDP_URL`): 報告句あり 3/3でカードまで押す。報告句を除いた同goalは
  2/2 fill後のfit True→`outcome_verified`、カードは押さない。

## 再現手順

E2E-06b Finitact試行1の3本目run(run-1625aab659ab、artifacts/e2e-live/e2e06b-n3-finitact-20260929)。final_stateのtitleが'Configure Amazon Quantum Ledger Database (QLDB)'。保存は無くCSVも無し

## 該当箇所

- `finitact/achievement.py` `EffectFitVerifier`(fitがFalseでも止める根拠にならず継続する)、providerの次手選択。

## 原因

- Finitactは報告という操作を持たないのに、goalが報告を完了条件に含むため、Jevは画面操作で報告を満たそうとして
  結果カードを押す。報告はrun結果の`final_state`で外側が行う役割で、goal契約の外。
- 直し方はgoalの操作部分と報告部分の分離(外側への説明か構造)で、保留中の「run分割の説明調整」(yo-xe、呪文化の懸念)と
  同じ判断に属するため、方針確認まで修正しない。

## 修正

- 2026-09-30 修正済み(コミット 4715824): goalに報告句があるとJevのfitがFalseで続行し結果カードを押す。報告はfinal_stateで外側が行うため、goal欄の説明に報告・確認を書かないよう明記(yo-xeが説明調整の保留を解除)。
