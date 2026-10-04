# BUG-0036: screen fillのcaret門(BUG-0033)が、既にフォーカス済みの空欄を誤拒否する。空のDiscord入力欄ではclickしてもcaretが欄の左端(#の位置、画面x=522)から動かず、OCR枠は#を除いた文字列(x=540〜)なのでslack 4pxの外と判定されkeyを送らない。拒否は同じexclusive_environment_refをプロセス内でstickyに遮断し、以後のrunもblocked/ValueErrorになる。run8/9はcaret_refusedなのにFillTargetRefusedでなくMutationUncertainと報告された

- 報告日: 2026-09-25 / 状態: 修正済み
- 重大度: 高 / 発生コミット: c275b50

## 症状

- (未記入)

## 再現手順

Discordを#testで開き入力欄を1度clickして空のまま、run_windows(screen:<Discord HWND>:<PID>, fill, fill_values)を呼ぶ。直接実行した_pointer_scriptの戻りはcaret_before=caret_after=[522,765,1,21]・caret_refused=True・key_events=0(run discord-cmp-5/8/9-post、2026-09-25)

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- 2026-09-25 修正済み(コミット 41875dc): 空の既フォーカス欄はcaretが欄先頭に留まり、OCR枠(#抜き)の左外で誤拒否された。caret門の左端を同じ行でline高2つ分まで許容(commit 41875dc)。live再計測でDiscord投稿成功。拒否が環境refをstickyに遮断する点・run8/9のMutationUncertain報告は未対応
