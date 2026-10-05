# BUG-0078: run_browserがChrome未起動だと開始できず、自動起動も代替経路も無いため外側が観察を諦める(BUG-0042は失敗の遅さのみ修正済み)

- 報告日: 2026-10-05 / 状態: 修正済み
- 重大度: 中 / 発生コミット: 411ca53

## 症状

- (未記入)

## 再現手順

Windows側でChromeを起動していない状態でWSLのClaude Codeから run_browser(start_url=https://galleria-wsl.tail14a219.ts.net/..., keep_tab=true)を呼ぶ → RuntimeError: chrome-not-running: no supported Chromium-family browser is running -- start Chrome, then retry(outcome=unverified, mutation_state=none)。2026-10-05 dsh-exp での実測(run-81e7b7194c51)。外側は人に Chrome 起動を頼むか別手段(WSL の playwright headless)へ回るしかなかった。期待: 起動を試みる/専用プロファイルで起動する、または起動可能性を事前に判別して返す

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-10-05 修正済み(コミット 411ca53): BUG-0042の先行確認がharnessの既定profile起動(Chrome 136+ではCDP不可)を塞ぎ、CDP待ち受けbrowserが無いと代替が無かった。CDPを待ち受ける利用者browserが無ければFinitact専用profileでChrome/Edgeを起動・再利用する(ADR-0054、finitact/browser_launch.py)。Windows実機: 初回2.3秒・再利用0.2秒・強制終了後3.5秒
