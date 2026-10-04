# ADR-0020: BLOCKEDは確信度0.6未満ならprovider_uncertainへ回す

- 日付: 2026-09-24
- 状態: 採択
- 決定者: Claude Code(ADR-0019の閾値は「他のcaseを足したら見直す」としていた。操作しない方向の変更なので単独で決めた)

## 背景

Unity再起動後、select caseが5/5 `provider_blocked`になった(plan `uncertain-direct-pick`)。同じ局面を固定frameで
再現すると(Windows側`artifacts/heat-selection/20260924b-unity-select/`)、候補91件でBLOCKEDが0.36〜0.47。
以前のframe(88件)では0.25〜0.33で`provider_uncertain`に落ちていた。BUG-0023の誤BLOCKEDが、ADR-0019の閾値0.4を
画面の小差でまたいだだけで、外側agentはlabelを受け取れず言い換えられない。

## 決定

1. 終端BLOCKEDは確信度0.6未満なら`provider_uncertain`(上位label付き)で終える。非終端の選択は従来どおり0.4、DONEは閾値なし。
2. `BLOCKED_CONFIDENCE`として`finitact/runs.py`に置く。

## 検討した代替案(没案)

- **単一閾値を0.5へ上げる**: 非終端の正答にBlender select-mode#1の0.46〜0.49があり、正しい操作まで止める。
- **BLOCKEDの閾値0.5**: 誤BLOCKEDの最大0.49との余白が0.01しかない。
- **provider_blockedにもlabelを付け外側agentに再試行させる**: 本当に不可能なgoalでも外側の往復とtokenが毎回増える。

## 影響

- 較正(`blocked-calibration.jsonl`、固定frame 9局面、Jev): 解けるgoalで誤ってBLOCKEDを選んだ時の確信度は最大0.49。
  不可能なgoal(メール送信・曲送り)のBLOCKEDは0.70〜0.95、別アプリのgoalは0.55〜0.71。
  0.55〜0.6のBLOCKEDは言い換え1回分の往復が増えるが操作はしない。
- 不可能goal「文書をreport.docxで保存」はBLOCKEDでなく"File"を0.48〜0.72で選ぶ。この閾値の対象外で、
  別途扱う(操作としてはメニューを開くだけ)。
- 余白は0.11。case追加で誤BLOCKEDが0.6へ近づいたら見直す。

<!-- 現況 -->
2026-09-24: 採択・実装済み(`finitact/runs.py`の`BLOCKED_CONFIDENCE`)。live(Windows側`artifacts/phase-i/adr0020-unity-x5`): open 5/5、select 3/4成功。失敗1件は別要因(STATUS参照)。
<!-- /現況 -->
