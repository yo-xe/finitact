# BUG-0037: uia経路のprovider_uncertainでscreen_candidatesのrefを返すが、UIA adapterにretainが無く観測を保持しないため、そのrefでのpickが必ず'pick: no retained observation'でblockedになる

- 報告日: 2026-09-26 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 33d24c0

## 症状

- (未記入)

## 再現手順

E2E-03 pilot(artifacts/e2e-live/20260926-e2e03-pilot): run_windows target uia:<Brave>、autofill候補のclick goalがconfidence 0.29でuncertain→返ったrefでpick→blocked。runs.py _retain は adapter.retain 無しで黙って保存しない

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-27 修正済み(コミット 84c1491): UIA adapterにretain/adoptが無く、uncertain時にrefを返しながら観測を保持していなかった。retainは空のframes、adoptは観測の候補を受け入れ、fresh()の再読とact()のruntime id照合で古い画面への配送を防ぐ
