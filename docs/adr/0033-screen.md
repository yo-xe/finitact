# ADR-0033: screen操作語彙の網羅と配送前の強制前面化

- 日付: 2026-09-25
- 状態: 採択
- 決定者: yo-xe + Claude Code

## 背景

E2E-01(`docs/plans/e2e-live.md`)で次の3つが顕在化した。
- double clickが無く、desktop iconの起動をclick→Enterで代替した。選択済みiconへの再clickでrename状態に入る。
  右click・hover・修飾click、F2・Delete・Backspace等のkeyも無い(E2E-I6)。
- screen配送は前面windowがtargetである時だけ通る。PowerShellが前面の状態からは、外側agentがtargetを
  明示してもdesktop iconを押せず`blocked`で終わった(E2E-I5)。windows-mcpは同じ状態で成功した。
- 入力前の拒否(caret門、BUG-0036)が`send()`内の例外として扱われ、MutationUncertain報告と
  環境refのsticky遮断を招いた(E2E-I1・I2)。

## 決定

- screen pathの操作語彙へpointer操作`double_click`・`right_click`・`middle_click`・`hover`・`ctrl_click`・
  `shift_click`を足す。各regionの候補は`allowed_operations`に含まれる操作だけ出す(候補数を要求側で抑える)。
- key allowlistを、1つのtarget window内で意味が閉じる操作の網羅へ広げる(編集・選択・移動・F1〜F12・
  tab/文書操作・context menu)。window外へ作用するkey(Win系、Alt+Tab、Alt+F4)は入れない。
- targetが分かっている配送では、機構が配送直前にtarget root windowを強制前面化する(yo-xe決定)。
  前面化は既存のlease・idle門の内側、配送前検証と同じnative呼び出しで行い、前面化後も従来の
  前面・hit-test検証を通った時だけ送る。
- 入力前に確定した拒否(caret門など)は`not_attempted`で返し、環境refを遮断しない。遮断は入力が
  部分的に届いた可能性がある時に限る。

## 検討した代替案(没案)

- 前面化を操作語彙(`activate`)として外側agentに選ばせる: target明示済みでも1往復増え、選び忘れで
  E2E-I5が再発する。
- 前面化を行わずblockedを返す(現行): 実運用ではtargetが前面にある保証が無く、E2Eで失敗した。
- 自由なkey chordを受け付ける: 候補の意味をレビューできず、Win+R等でtarget外へ作用し得る。
- Alt+F4をallowlistへ入れる: desktop(Progman)ではシャットダウン画面を開く。closeはボタンclickで足りる。
- 全pointer操作を常に候補にする: region数×操作数でprovider入力が膨らむ(速度第一)。

## 影響

- `allowed_operations`に新しい操作名を渡せる。UIA pathはpattern-onlyのまま変えない。
- key候補が増えprovider入力tokenが増える。E2Eの内部tokenで監視し、過大なら要求側でkey集合を絞る
  仕組みを別途決める。
- desktop(Progman)は前面化しても他windowに覆われたiconはhit-testで拒否される(安全側)。

## 追記1(2026-09-28、BUG-0040)

- 前面化はlock timeout解除に`AttachThreadInput`(前面windowのthreadへ一時接続)を併用する。native・PowerShell両経路。
- 理由: 端末が最後の実入力を持つ間、常駐MCP serverの解除だけの前面化はtaskbarで3/3失敗、通常windowでも
  間欠的に失敗した(起動直後のprocessは許可されるため単発probeでは再現しない)。併用で両targetとも6/6・6〜9ms。
  過去に`AttachThreadInput`単独が効かなかった事例(本ADR)があるため置換せず併用とした。

<!-- 現況 -->
2026-10-04: 実装済み。pointer 7種(`screen_grounded_adapter.POINTER_OPERATIONS`)、key allowlist、
配送前の強制前面化(`windows_screen_grounded.py`の`FinitactPointer::Front`)。
<!-- /現況 -->
