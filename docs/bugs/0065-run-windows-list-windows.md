# BUG-0065: run_windowsの窓タイトル指定が、同名の最小化窓があると曖昧エラーになり外側がlist_windowsで往復する

- 報告日: 2026-09-30 / 状態: 修正済み
- 重大度: 低 / 発生コミット: e811334

## 症状

- (未記入)

## 再現手順

Blenderを1つ最小化で開いたまま screen-blender-workspace-001 をFinitactで実行(c1-20260930f-finitact t1・t2: run_windows→list_windows→run_windowsの3 call、外側約22k。単独時13.6k)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-30 修正済み(コミット 218127f): resolve_windowが一致窓のうち非最小化が1つだけならそれを採る(218127f)。C1 Blender workspace 外側22.0k→10.3k・1 call
