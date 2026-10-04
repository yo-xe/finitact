# BUG-0024: Blenderのoracle用state writerがos.replaceのPermissionErrorでtimerごと停止する

- 報告日: 2026-09-24 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 96c7f66

## 症状

- (未記入)

## 再現手順

run_phase_i_comparison.py finitact --case screen-blender-workspace-001 実行中、oracleがstate.jsonを開いている瞬間にwriterのrenameが衝突(smoke4_err.txt)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-24 修正済み(コミット 96c7f66): bpyのtimerは例外で登録解除される。WindowsではoracleがFileを開いている間os.replaceがPermissionErrorになり、writerが止まってoracleが判定不能になった。renameの失敗は次tickへ持ち越す(scripts/blender_state_writer.py)。smoke4bでpoll error 0
