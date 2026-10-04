# BUG-0061: C1 VS Code fill-save で Finitact が2/10 failure: action_budget=2 を2 mutation confirmed で使い切り provider_done に至らず、保存ファイルは OLDVALUE のまま(保存が送られていない)。redacted journal では2手の中身が判らない

- 報告日: 2026-09-30 / 状態: 修正済み
- 重大度: 中 / 発生コミット: f262aa2

## 症状

- trial 4・8でJevがfillを2回選んだ(`mcp-server.log`の`acts`行)。2回目は新しい本文を既に表示している編集欄への同文fillで、Ctrl+Sの前にbudget 2を使い切った。成功8件はfill→Ctrl+S。

## 再現手順

2026-09-30 09:40頃 run_phase_i_comparison.py finitact --case screen-vscode-fill-save-001 --trials 10。trial 4・8。記録 windows-checkout/artifacts/phase-i/c1-20260930c-finitact-screen-vscode-fill-save-001/records.jsonl

## 該当箇所

- `finitact/runs.py` WindowsRunCoordinatorの候補除外(`spent`)

## 原因

- 同goalで届けたfill文を既に表示している欄へのfillが候補に残り、無変化の再fillをJevが選べた。

## 修正

- 2026-09-30 修正済み(コミット afe412a): Jevが本文を既に置き換えた編集欄へ同文を再fillしbudget 2を使い切っていた。届けたfill文を表示済みの欄を再fill候補から外した。live N=10で10/10(c1-20260930d)
