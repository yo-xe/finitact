# ADR-0022: provider_uncertain後は外側agentの候補直接指定を主、言い換えを予備にする

- 日付: 2026-09-24
- 状態: 採択
- 決定者: yo-xe + Claude Code(`docs/plans/uncertain-direct-pick.md`の承認。ADR-0019の案a2没を部分的に覆す)

## 背景

ADR-0019の言い換え(a1)はUnity select×5で5/5成功したが、2回目の呼び出しがobserve(OCR約3.6秒+capture)と
判断をやり直すため、windows-mcpよりselectで1.5倍遅い(`phase-i-comparison.md`「再run」、plan手順1の計測)。
a2没の理由は(1)同一観測を別runで再同定する契約が無い、(2)"Game"の誤選択。(2)はlabelに位置・種別が無かったことが一因と見る。

## 決定

1. `provider_uncertain`の結果は`screen_labels`に代えて`screen_candidates`(`ref`=候補id・`label`・`rect`・`popup`、
   label重複除去・edge候補除外の上位5件)を返す。
2. `run_windows`に`pick {run_id, ref}`を足す。coordinatorはuncertainで終えたrunの観測(frames+候補)をprocess内に
   4件まで保持し、pickは1回で消費する。初手はproviderを通さずその候補を`fresh`→`act`し、以降は通常loop(DONE判断)。
3. fail closed: 保持なし・target_id不一致・提示外のref・adopt非対応adapterは`blocked`(detailが`pick:`)で操作しない。
   候補の窓が変わっていれば(`fresh`がSTALE)操作せず`blocked`。配送前の再capture+anchor照合は通常手と同じ。
4. 外側agentは候補が目的の要素なら直接指定し、該当が無いか`pick:`で止まった時だけ言い換える(a1は予備)。

## 検討した代替案(没案)

- **言い換えのみ(ADR-0019の現状)**: 成功率は十分だが、2回目の観測と判断の分だけ遅い。
- **pick時も観測を取り直してrefを再同定**: OCR候補idは画素不変領域でも揺れる(Phase G)ので、取り直しは
  再同定失敗を増やし、省きたいobserveも残る。保持した観測を正とし、窓の不変で照合する。
- **観測の永続化(ledgerへframes保存)**: 1件が数MBで、再起動後の画面が同じ保証も無い。process内に限る。

## 影響

- pick runの初手はobserve 1回と判断1回を省く(DONE判断は残る)。記録は`metrics.direct_picks`で区別する。
- 誤指定("Game"型)を外側agentが犯すと誤操作になりうる。検証はplan手順5(Unity 2 case×5+Blender回帰)で行う。

<!-- 現況 -->
2026-09-24: 採択・実装済み(`finitact/runs.py`の`CandidatePick`・`_retain`/`_adopt`、
`ScreenGroundedAdapter.retain/adopt`、outer_agentのprompt)。live: Unity 2 case×5で10/10、pick 7件は誤指定0、
Blender回帰6/6(`phase-i-comparison.md`「ADR-0022後」)。
<!-- /現況 -->
