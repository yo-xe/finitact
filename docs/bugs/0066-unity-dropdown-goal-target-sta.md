# BUG-0066: Unity dropdownを開くgoalで開いた後も送信を繰り返し、target stale/ambiguousでblocked・mutation_state mixedになる

- 報告日: 2026-09-30 / 状態: 修正済み
- 重大度: 中 / 発生コミット: e811334

## 症状

- (未記入)

## 再現手順

c1-20260930f-finitact screen-unity-open-dropdown-001 t2(run-4df9d0451e3e): mutation_attempts 4・outcome unverified。外部判定は成功。harnessの安全停止でbatchが止まった

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-30 修正済み(コミット 218127f): クリック後に新しく現れたowned popupの項目をEの着地事実にしfitを問う(218127f)。Unity dropdown 9/10(失敗1はprovider接続)・送信1回・68.1→10.0秒

## 追記(2026-09-30 計測中)

- 同じ run で r161544 の t2・t3 は 1 run 内で送信 24・25 回、deadline 終了(約 70 秒)。t1・t4 は送信 1 回・provider_done(約 17〜19 秒)。
  開いた dropdown(別 popup)を達成と認めず押し直し、開閉を繰り返していると推定。最終状態が開なら外部判定は成功になる。
- 前回 C1(`c1-20260930c-finitact-screen-unity-open-dropdown-001`)は prompt 統一前で 10/10 が送信 1 回。
- 外側 agent が開いた後に `Click Full HD (1920x1080)` を追加で実行した試行あり(r161544 t2)。
