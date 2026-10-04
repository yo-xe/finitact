# BUG-0001: browser mutationの途中失敗がhistoryに残らず未実行と区別できない

- 報告日: 2026-09-21 / 状態: 修正済み
- 重大度: 高 / 発生コミット: 049afb8

## 症状

- browser input開始後に例外が起きるとdecisionだけが消費され、historyに実行記録が残らない。
- 呼び出し側は入力前拒否とpartial mutationを区別できず、安全な自動再試行判定ができない。

## 再現手順

Agent.command('act')でBrowser.actが入力開始後にRuntimeErrorを送出し、historyと別のattempt記録が無いことを確認する

## 該当箇所

- `jev_ultrafast/agent.py` の `Agent.command("act")`

## 原因

- action historyを`Browser.act()`の正常return後にだけ作っており、送信前intentを持つ別台帳がなかった。

## 修正

- 2026-09-21 修正済み(コミット `2894ce5`): mutation intentをbrowser input前に記録し、成功を
  `confirmed`、入力前staleを`not_attempted`、その他の実行例外を`uncertain`としてrun停止するよう修正。
  offline回帰40件とbrowser guard 23件を通過。
