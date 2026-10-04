# BUG-0076: TypeSafeへの接続例外(httpx.HTTPError)が再試行されずrunがerror停止する。2026-10-04 C1取り直し(b3c1b74)でunity-open-dropdownのFinitact 8試行中3回が'Model connection failed'(provider_attempts=2、操作前)で失敗。429/503/529は再試行するのに接続例外は即raise(finitact/model.py:59-67)。例外型はMCP server内journalにしか残らず未特定

- 報告日: 2026-10-04 / 状態: 未修正
- 重大度: 中 / 発生コミット: e33c73d

## 症状

- (未記入)

## 再現手順

Windows側でrun_phase_i_comparison.py finitact --case screen-unity-open-dropdown-001 --trials 10を回すと数試行に1回、goal detailが'RuntimeError: Model connection failed; no action executed.'(artifacts/phase-i/c1-20261004-finitact-r2・r3・r5)

## 計測上の扱い

- 2026-10-04のC1取り直しで3件発生(Unity open-dropdown、`finitact-r2`・`r3`・`r5`の各1試行。run_id末尾`1791100656`・`1791103568`・`1791104738`)。
  いずれもgoalは`termination_reason=error`で操作は未実行。yo-xeの指示でUI試行に数えず、`valid=false`で除外し、
  同caseを3試行追加(`finitact-r7-1`〜`3`、全て成功)。元のrecordは`records.jsonl.pre-bug0076`に保存。
  以後のrunnerは`outer_agent.provider_unreachable`で同じ除外を自動記録する。発生回数は本BUGの未解決の証拠として残す。

## 該当箇所

- (未調査。当たりを付けるには dev map を使う)

## 原因

- (未調査)

## 修正

- (dev bug close で記入される)
