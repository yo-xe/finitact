# BUG-0075: Windows run: click確定後のprovider例外でgoal結果がmutation_state=none・unmet_effects空になる

- 報告日: 2026-10-04 / 状態: 修正済み
- 重大度: 高 / 発生コミット: ed9efbc

## 症状

- (未記入)

## 再現手順

使い捨てWinForms窓で規則で戻るtoggleをclick(confirmed)→次のdecideでInvalid TypeSafe response→goalはerror/none(EXP-0014 smoke)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-10-04 修正済み(コミット ed9efbc): WindowsRunCoordinatorのdecide例外がgoal loop外の一括catchへ抜け、確定済みmutationとunmet_effectsを捨てていた。decide呼び出しをloop内で捕捉しerror/budgetとしてbreakし、通常の結果組立てを通す(browser経路と同じ扱い)
