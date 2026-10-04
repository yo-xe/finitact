# BUG-0062: E2E-04の判定器がEdgeの記事末尾(最終更新・プライバシー・ポリシー)をUIAで見つけられずfound=Falseとし、末尾表示済みのwindows-mcp試行を失敗と誤判定する

- 報告日: 2026-09-30 / 状態: 未修正
- 重大度: 中 / 発生コミット: 39d249f

## 症状

- (未記入)

## 再現手順

2026-09-30 C2 run_e2e_live.py windows-mcp --scenario E2E-04 --trials 5 の試行1・5。最終screenshotのOCRには両文言がある(artifacts/e2e-live/c2-20260930-E2E-04-windows-mcp)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- (dev bug close で記入される)

## 検証状況

- 再走査(最大3回・`walks`記録)を入れた後の取り直し`c2-20260930b-E2E-04-windows-mcp`は5/5で全件walks=1。再走査は一度も効いておらず修正の効果は未確認。偽陰性の再発時にwalks>1で成功するかを見てから閉じる。
