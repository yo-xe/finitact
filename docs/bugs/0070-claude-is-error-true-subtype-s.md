# BUG-0070: Claude週上限のis_error=true subtype=successがC1/C2評価で有効試行に入る

- 報告日: 2026-10-02 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 8d2c607

## 症状

- (未記入)

## 再現手順

claude.aiログインの週上限中にsonnetを1回呼ぶとresultにhit your weekly limit、is_error=true、subtype=success。C1/C2 runnerのlimit除外条件を通らない

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-10-02 修正済み(コミット 8d2c607): Claudeのis_error=true・subtype=success・weekly limitをusage_limitで検出し、C1/C2ともvalid=false・excludedに記録して停止。固定結果の単体試験を追加しdev test 609件通過。
