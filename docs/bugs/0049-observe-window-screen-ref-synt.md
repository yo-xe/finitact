# BUG-0049: observe_window(screen経路で保持)のrefを synthetic_input_allowed=false のrun_windowsへ渡すと 'pick: no observation of this window' になり、外側が同じ呼び出しを繰り返す

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 4f0a5b2

## 症状

- (未記入)

## 再現手順

E2E-10 Finitact N=3(artifacts/e2e-live/e2e10-n3-finitact-bug47-20260929) 試行1・2で計6回。runs._take_retained が uia:キーで引くため。対処案: 経路不一致を名指す拒否文にするか両経路で引く

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット f2bd645): 原因: observe_windowは常にscreen経路でrefを保持し、uia経路のrunはuia:キーで引くため'no observation'と誤った理由で拒否していた。対処: refを窓単位(HWND:PID)で保持し、経路が違えばsynthetic_input_allowedを名指す拒否文にする。どちらの経路のrunでもその窓のrefは終わる
