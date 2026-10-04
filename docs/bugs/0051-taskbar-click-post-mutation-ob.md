# BUG-0051: taskbarのスタートclick後、post-mutation observationがValueErrorで失敗(原因未特定。例外型しか記録されない)

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 4f0a5b2

## 症状

- (未記入)

## 再現手順

E2E-10 Finitact N=3(artifacts/e2e-live/e2e10-n3-finitact-bug47-20260929) 試行1 run-d49cfb3cc67a。単独のobserve_windowは開閉とも成功するのでrun内のadapter.observe()経路。runs.py:1275で例外をlogしていない

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- E2E-10再計測(`artifacts/e2e-live/e2e10-n3-finitact-bug52-20260929/` 試行3)で最初のobserveに再現。Windows側`~/.local/state/finitact/mcp-server.log`のtraceback: observe(screen_grounded_adapter.py:300)→_fill_candidates(1139)→_candidate(1103)→_checked_rect(1568)。OCR由来のfill regionがframe外にはみ出す(未被覆OCRのdx/dyオフセットか、frameとUIA読取時のwindow rect差が疑い)。1 regionの不正でobserve全体が落ちる構造も直す対象

## 修正

- 2026-09-29 修正済み(コミット 992f497): OCR regionがframe外に出るとVisualRegion検証でValueError。frameへclipし外側は捨てる。live: taskbar click後のobserveが3/3で107候補(artifacts/e2e-live/e2e10-shell-repair-20260929/live3.jsonl)
