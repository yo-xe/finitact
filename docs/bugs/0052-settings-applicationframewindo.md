# BUG-0052: Settings(ApplicationFrameWindow)が前面の時、taskbarのStart clickが'foreground stayed on ... 設定'でnot_attemptedになる(_frontのAttachThreadInput+SetForegroundWindowでもtaskbarを前面化できない)

- 報告日: 2026-09-29 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 9caa480

## 症状

- (未記入)

## 再現手順

設定アプリを前面にしてWindows側 probe_bug51.py(windows-checkout直下、taskbarをscreen経路でobserve→スタートをclick→再observe)を実行。2/2で再現。同probeではpost-mutation observeは成功しBUG-0051は再現せず

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-29 修正済み(コミット 027d72d): 原因: 設定(ApplicationFrameHost)のthreadへのAttachThreadInputがERROR_ACCESS_DENIEDで失敗し、自プロセスが直近入力を持たないためSetForegroundWindowが拒否された。対処: attachが拒否された時は0移動のマウス入力を先に送り直近入力を得てから前面化する(in-process・PowerShell両経路)。SwitchToThisWindow単独は無効だった。live: 設定前面でStart click 3/3 confirmed、PowerShell経路focus_is_valid 修正前0/2→修正後2/2
