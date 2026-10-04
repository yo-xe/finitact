# BUG-0053: taskbar検索欄のfillがクリック後SearchHostへ前面移動してkey未配送となり環境をblockする

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 992f497

## 症状

- (未記入)

## 再現手順

artifacts/e2e-live/e2e10-n3-finitact-bug52-20260929 試行1。taskbar検索欄へfill。クリック後のSearchHostが事前foreground集合に無い。

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 992f497): click後に前面が許可集合外へ移りkey未送信が確定した時だけFillFocusChangedでuncertain(環境blockなし)とし、新窓の再観測へ戻す(consult 20260929-1528)。部分送信・応答欠落は従来通りblock。live 3/3でSearchHost再observe後のfillがconfirmed・値一致(artifacts/e2e-live/e2e10-shell-repair-20260929/live3.jsonl)
