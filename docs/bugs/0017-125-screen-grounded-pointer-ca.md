# BUG-0017: 表示倍率125%でscreen-grounded pointerが別座標をクリックする: captureはAutomationElement.FromHandleの副作用でDPI-aware(物理座標)、pointer scriptはDPI非対応(論理座標)のままGetWindowRect+SetCursorPosするため、窓局所点がscale倍ずれる。hit-testはroot一致で通るため誤クリックがconfirmedになる

- 報告日: 2026-09-23 / 状態: 修正済み
- 重大度: 高 / 発生コミット: da99596

## 症状

- (未記入)

## 再現手順

DISPLAY1を125%に変更(scripts/display_scale.py)→Unity(物理1800x1028)で窓局所(618,150)へSetCursorPosをpointer scriptと同じ非aware PowerShellで実行→実物理局所は(772,188)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-23 修正済み(コミット da99596): 原因: DPI awarenessをUIA FromHandleの副作用に依存し、pointer scriptだけ非awareだった。対処: screen-grounded全scriptの冒頭でSetProcessDpiAwarenessContext(PMv2)、得られなければthrow。125%/100% live probe pass
