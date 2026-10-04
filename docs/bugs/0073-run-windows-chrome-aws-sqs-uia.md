# BUG-0073: run_windowsでChrome上のAWS計算機SQS欄(UIA名なしのnumber input)へ入力できず、E2E-02 Finitact失敗3/3の直接原因。候補はOCRのplaceholder『Enter amount』と窓全体rectの『Enter Amount』で、fillはconfidence 0.14〜0.39で停止、外側agentの『focused fieldへキー入力』goalはアドレスバーへ入りGoogle検索候補が出た(run-78470ba0d963はoutcome_verified)

- 報告日: 2026-10-03 / 状態: 未修正
- 重大度: 中 / 発生コミット: d98d5c1

## 症状

- (未記入)

## 再現手順

run_e2e_live.py finitact --scenario E2E-02(2026-10-03、c2-20261003-E2E-02-finitact試行1791029439ほか)。promptはgoal+『Chrome windowで開いている』のみ(7c4748a)。外側agentがrun_windowsを選ぶとSQS Standard queue requestsで詰まり、run_browserへ切り替えた2試行だけ成功

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- (dev bug close で記入される)
