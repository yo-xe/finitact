# BUG-0069: scroll探索中に別行Item 15を選択してもoutcome_verifiedと報告する

- 報告日: 2026-10-01 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 3f46f0e

## 症状

- 独立oracleが`Item 15 entry`を示し、目標`TARGET ROW`はまだ画面外なのに、run結果は`outcome_verified/verified_success`になる。本試行3/3。誤った行のクリックが確認済みであり、達成判定も偽陽性。

## 再現手順

EXP-0012の60行Tk ListboxでGOAL='Scroll down until TARGET ROW is visible, then select it'、現行共通runループとTypeSafe、screen経路を実行する。1回scroll後、target未表示のままItem 15 entryをclickし、独立oracleはItem 15 entryだがrun結果はoutcome_verified/verified_success。本試行3/3。docs/evaluations/scroll-loop/exp0012-common.md参照。

## 該当箇所

- `finitact/achievement.py`の`ArrivalFitVerifier`と、`finitact/runs.py`のaction verifier結果による終端。

## 原因

- 追加の検証器別pilotで、scroll後の誤クリックに対して`ArrivalFitVerifier`が`True`を返すことを確認。`TerminalEvidenceVerifier`はscroll時に`None`。TypeSafeのfit判断が目標の対象同一性を誤認し、共通ループがその正判定を達成として採用した。候補側には次のscroll downが残っていた。

## 修正

- 2026-10-01 修正済み: screen clickの画素変化のみでは達成へ昇格しない終状態ゲートを追加。元goal/fixtureの実TypeSafe N=3で誤成功3/3→0/3、誤行click自体は3/3残存。正しい行の制御選択も独立oracle成功だがUIA選択状態がなくunverified。
