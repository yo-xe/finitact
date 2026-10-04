# BUG-0003: model-call budgetが成功decisionだけを数えprovider retryと失敗attemptを除外する

- 報告日: 2026-09-21 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 37c692a

## 症状

- providerがretryable responseを返すと、1 logical decisionから最大3 HTTP attemptが発生するが、
  従来budgetは成功して`decisions`へ入った回数だけを数えていた。

## 再現手順

503 retryが続くpost_jsonを実行し、従来のlen(decisions)上限では各HTTP attemptを合算できないことを確認する

## 該当箇所

- `jev_ultrafast/model.py::post_json`
- `jev_ultrafast/agent.py::Agent.command("predict")`

## 原因

- transport retryとlogical decisionの台帳が分かれておらず、安全上限の単位が実network送信と一致しなかった。

## 修正

- 2026-09-21 修正済み: TypeSafeとtext helper共通のsanitized `model_attempts` journalを追加し、
  120 HTTP attemptの合算上限を送信前に強制。text helper停止を含む50 tests成功。
