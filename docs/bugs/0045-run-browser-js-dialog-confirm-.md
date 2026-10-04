# BUG-0045: run_browserがJS dialog(confirm等)で止まる。Deleteでconfirmが開くと以降のrun_browser・observe_browserがCDP timeout(Emulation.setFocusEmulationEnabled等)になり、dialogを閉じる手段が無い

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 中 / 発生コミット: ecf4a51

## 症状

- (未記入)

## 再現手順

run_e2e_live.py finitact --scenario E2E-08(artifacts/e2e-live/e2e08-n3-finitact-20260929)。0/3、各21〜30秒でBLOCKED報告。Page.javascriptDialogOpeningを扱っていない(scenarios.md E2E-08の想定通り)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 2ab5a24): dialog(alert/confirm/beforeunload/prompt)を有限候補として観測・応答する(ADR-0047)。live初回はdialog候補にnodeが無くJevのaction_spaceがKeyErrorで落ちたため候補idを要素キーにした。E2E-08 Finitact 3/3
